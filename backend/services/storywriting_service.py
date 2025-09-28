"""Storywriting service handling multi-step story interactions."""

import json
import logging
from dataclasses import dataclass, field
from typing import List, Optional, Sequence

from content_manager import content_manager
from llm_provider import llm_provider
from prompt_manager import prompt_manager
from telemetry import collect_educational_data, get_latest_llm_timing, latency_logger
from utils import extract_topic_from_message, get_theme_suggestion
from vocabulary_manager import vocabulary_manager

from .design_service import (
    handle_design_phase_interaction,
    trigger_enhanced_design_phase,
    validate_entity_structure,
)
from .story_parser import parse_enhanced_story_response
from .vocabulary_service import (
    extract_vocabulary_from_content,
    log_vocabulary_debug_info,
    select_best_vocabulary_word,
)
from models.schemas import (
    ChatResponse,
    EnhancedStoryResponse,
    SessionData,
    StoryEntities,
    StorywritingPhase,
    VocabQuestion,
    VocabularyPhase,
)


logger = logging.getLogger(__name__)


# === Story Context Objects ===


@dataclass
class StoryTurnContext:
    session: SessionData
    topic: Optional[str]
    story_mode: str = "auto"
    user_message: Optional[str] = None
    intro_prefix: Optional[str] = None

    def story_window(self, size: int = 3) -> str:
        return "\n".join(self.session.storyParts[-size:])

    @property
    def vocab_pool(self) -> List[str]:
        return self.session.askedVocabWords + self.session.contentVocabulary


@dataclass
class PromptPlan:
    prompt: str
    tags: Sequence[str]
    should_end_story: bool = False
    sub_interaction: int = 1


@dataclass
class PromptOutcome:
    text: str
    duration: float
    selected_vocab: List[str] = field(default_factory=list)


@dataclass
class StoryParseResult:
    story: str
    structured: EnhancedStoryResponse
    vocabulary_words: List[str]
    had_parse_error: bool = False


@dataclass
class DesignDecision:
    should_trigger: bool
    structured_payload: EnhancedStoryResponse


# === Shared Helper Utilities ===


def _enhance_prompt_with_vocab(
    base_prompt: str, topic: Optional[str], vocab_sources: Sequence[str]
) -> tuple[str, List[str]]:
    enhanced_prompt, selected_vocab = prompt_manager.enhance_with_vocabulary(
        base_prompt,
        topic,
        vocab_sources,
    )
    return enhanced_prompt, selected_vocab or []


def _extend_content_vocabulary(
    session_data: SessionData, new_words: Sequence[str], context_label: str
) -> None:
    if not new_words:
        return

    session_data.contentVocabulary.extend(new_words)
    logger.info(
        "📋 VOCABULARY TRACKING: %s - Added %s words. Total tracked: %s",
        context_label,
        len(new_words),
        len(session_data.contentVocabulary),
    )


def _log_vocabulary_snapshot(
    topic: Optional[str], session_data: SessionData, story_text: str, stage_label: str
) -> None:
    log_vocabulary_debug_info(
        topic,
        session_data.askedVocabWords,
        story_text,
        stage_label,
        len(session_data.contentVocabulary),
    )


def _log_educational_interaction(
    event_type: str,
    response_text: str,
    duration: float,
    session_data: SessionData,
    user_message: str,
    *,
    sub_interaction: int,
) -> None:
    educational_data = collect_educational_data(
        session_data,
        ChatResponse(response=response_text),
        "storywriting",
        user_message,
        [event_type],
        sub_interaction=sub_interaction,
    )
    latency_logger.log_educational_interaction(
        event_type,
        response_text,
        duration,
        educational_data,
    )


def _prepend_feedback(feedback: Optional[str], story_text: str) -> str:
    if not feedback:
        return story_text
    return f"{feedback}\n\n{story_text}"


def _execute_story_prompt(
    plan: PromptPlan,
    context: StoryTurnContext,
    vocab_sources: Sequence[str],
    *,
    apply_vocab: bool = True,
) -> PromptOutcome:
    prompt_to_send = plan.prompt
    selected_vocab: List[str] = []

    if apply_vocab:
        prompt_to_send, selected_vocab = _enhance_prompt_with_vocab(
            plan.prompt,
            context.topic,
            vocab_sources,
        )

    response_text = llm_provider.generate_response(prompt_to_send)
    duration = get_latest_llm_timing()
    return PromptOutcome(
        text=response_text,
        duration=duration,
        selected_vocab=list(selected_vocab),
    )


def _parse_story_response(raw_response: str) -> StoryParseResult:
    parse_error = False
    try:
        enhanced_response = parse_enhanced_story_response(raw_response)
    except ValueError:
        parse_error = True
        logger.error("❌ ENTITY PARSE: Enhanced parser failed, using raw response fallback")
        enhanced_response = EnhancedStoryResponse(
            story=raw_response,
            entities=StoryEntities(),
            vocabulary_words=[],
        )

    logger.info(
        "✅ ENTITY PARSE: Characters named=%s, unnamed=%s",
        len(enhanced_response.entities.characters.named),
        len(enhanced_response.entities.characters.unnamed),
    )
    logger.info(
        "✅ ENTITY PARSE: Locations named=%s, unnamed=%s",
        len(enhanced_response.entities.locations.named),
        len(enhanced_response.entities.locations.unnamed),
    )
    if enhanced_response.vocabulary_words:
        logger.info("🎯 ENTITY DEBUG: Vocabulary words: %s", enhanced_response.vocabulary_words)

    if parse_error:
        logger.error("⚠️ ENTITY PARSE: Fallback response used due to parsing failure")

    vocabulary_words = list(enhanced_response.vocabulary_words or [])
    return StoryParseResult(
        story=enhanced_response.story,
        structured=enhanced_response,
        vocabulary_words=vocabulary_words,
        had_parse_error=parse_error,
    )


def _evaluate_design_decision(
    parse_result: StoryParseResult, story_mode: str
) -> DesignDecision:
    should_trigger = validate_entity_structure(parse_result.structured.entities)
    logger.info(
        "🎯 DESIGN PHASE DEBUG: validate_entity_structure() returned: %s",
        should_trigger,
    )
    skip_msg = "🎯 DESIGN PHASE DEBUG: Skipping design phase because story_mode='named'"

    if story_mode == "named" and should_trigger:
        logger.info(skip_msg)
        should_trigger = False

    return DesignDecision(
        should_trigger=should_trigger,
        structured_payload=parse_result.structured,
    )


def _record_story_generation(
    context: StoryTurnContext,
    parse_result: StoryParseResult,
    stage_label: str,
    metadata_label: str,
    fallback_label: str,
) -> None:
    context.session.storyParts.append(parse_result.story)

    if parse_result.vocabulary_words:
        _extend_content_vocabulary(
            context.session, parse_result.vocabulary_words, metadata_label
        )
    else:
        story_vocab_words = extract_vocabulary_from_content(
            parse_result.story, context.session.contentVocabulary
        )
        _extend_content_vocabulary(context.session, story_vocab_words, fallback_label)

    _log_vocabulary_snapshot(context.topic, context.session, parse_result.story, stage_label)


def _activate_design_phase_if_needed(
    context: StoryTurnContext,
    decision: DesignDecision,
    *,
    stage_label: str = "DESIGN PHASE",
) -> Optional[ChatResponse]:
    # if not decision.should_trigger:
    #     logger.info(
    #         "❌ %s: Skipping design phase - no designable entities found",
    #         stage_label,
    #     )
    #     return None

    _transition_phase(context.session, StorywritingPhase.DESIGNING)

    total_entities = (
        len(decision.structured_payload.entities.characters.unnamed)
        + len(decision.structured_payload.entities.locations.unnamed)
    )
    logger.info(
        "✅ %s: Triggering design phase with %s designable entities",
        stage_label,
        total_entities,
    )
    design_response = trigger_enhanced_design_phase(
        context.session, decision.structured_payload
    )

    design_response.suggestedTheme = get_theme_suggestion(context.topic)
    return design_response


def _prepare_session_for_topic(session_data: SessionData, topic: Optional[str]) -> None:
    session_data.topic = topic
    session_data.currentStep = 2


async def handle_storywriting(user_message: str, session_data: SessionData, story_mode: str = "auto") -> ChatResponse:
    """Orchestrate storywriting interactions across the different phases."""
    return await _dispatch_story_phase(user_message, session_data, story_mode)


async def _dispatch_story_phase(
    user_message: str, session_data: SessionData, story_mode: str
) -> ChatResponse:
    _log_phase_state("dispatch_start", session_data, user_message)

    vocabulary_response = await _handle_vocabulary_command(user_message, session_data)
    if vocabulary_response:
        _log_phase_state("dispatch_vocabulary", session_data, user_message)
        return vocabulary_response

    if not session_data.topic or session_data.conversationPhase == StorywritingPhase.NOT_STARTED:
        _log_phase_state("dispatch_topic_selection", session_data, user_message)
        return await _handle_topic_initialization(user_message, session_data, story_mode)

    if _is_design_phase_active(session_data) or session_data.conversationPhase == StorywritingPhase.DESIGNING:
        _log_phase_state("dispatch_design", session_data, user_message)
        _transition_phase(session_data, StorywritingPhase.DESIGNING)
        return await handle_design_phase_interaction(user_message, session_data)

    if (
        session_data.conversationPhase == StorywritingPhase.WRITING
        and not session_data.isComplete
    ):
        _log_phase_state("dispatch_writing", session_data, user_message)
        return await _handle_story_continuation(user_message, session_data)

    if session_data.isComplete or session_data.conversationPhase == StorywritingPhase.COMPLETED:
        _log_phase_state("dispatch_completed", session_data, user_message)
        return await _handle_completed_story_flow(user_message, session_data)

    _log_phase_state("dispatch_fallback_writing", session_data, user_message)
    return await _handle_story_continuation(user_message, session_data)


async def _handle_vocabulary_command(
    user_message: str, session_data: SessionData
):
    command_map = {
        "start_vocabulary": handle_start_vocabulary,
        "next_vocabulary": handle_next_vocabulary,
        "finish_vocabulary": handle_finish_vocabulary,
    }

    handler = command_map.get(user_message)
    if handler:
        if user_message in {"start_vocabulary", "next_vocabulary"}:
            _transition_phase(session_data, StorywritingPhase.VOCABULARY)
        logger.info(
            "🧭 PHASE VOCABULARY: Handling '%s' with questionsAsked=%s",
            user_message,
            session_data.vocabularyPhase.questionsAsked,
        )
        return await handler(session_data)

    logger.debug(
        "🧭 PHASE VOCABULARY: Ignoring message '%s' (no vocabulary handler)",
        user_message,
    )
    return None


def _is_design_phase_active(session_data: SessionData) -> bool:
    phase_value = getattr(session_data, "conversationPhase", StorywritingPhase.NOT_STARTED)
    if isinstance(phase_value, str):
        try:
            phase = StorywritingPhase(phase_value)
        except ValueError:
            phase = StorywritingPhase.NOT_STARTED
    else:
        phase = phase_value or StorywritingPhase.NOT_STARTED

    if phase == StorywritingPhase.DESIGNING:
        return not session_data.designComplete

    return bool(session_data.designPhase and not session_data.designComplete)


def _transition_phase(session_data: SessionData, new_phase: StorywritingPhase) -> None:
    """Update the conversation phase with logging when it changes."""
    current_phase = session_data.conversationPhase
    if current_phase != new_phase:
        logger.info(
            "🔄 CONVERSATION PHASE: %s → %s",
            current_phase,
            new_phase,
        )
        session_data.conversationPhase = new_phase


def _log_phase_state(stage: str, session_data: SessionData, user_message: str) -> None:
    logger.info(
        "🧭 PHASE STATE [%s]: phase=%s topic=%s isComplete=%s awaitingConfirmation=%s designPhase=%s message='%s'",
        stage,
        getattr(session_data, "conversationPhase", StorywritingPhase.NOT_STARTED),
        session_data.topic,
        session_data.isComplete,
        session_data.awaiting_story_confirmation,
        session_data.designPhase,
        user_message,
    )


# === Topic Initialization Flow ===


async def _handle_topic_initialization(
    user_message: str, session_data: SessionData, story_mode: str
) -> ChatResponse:
    topic = extract_topic_from_message(user_message)
    _prepare_session_for_topic(session_data, topic)

    context = StoryTurnContext(
        session=session_data,
        topic=topic,
        story_mode=story_mode,
    )
    plan = PromptPlan(
        prompt=prompt_manager.get_story_opening_prompt(topic, story_mode),
        tags=["story_generation"],
        sub_interaction=1,
    )
    prompt_outcome = _execute_story_prompt(
        plan,
        context,
        session_data.askedVocabWords,
    )

    logger.info(
        "🎯 LLM RESPONSE DEBUG: Raw response length: %s characters",
        len(prompt_outcome.text),
    )
    logger.info(
        "🎯 LLM RESPONSE DEBUG: First 200 chars: %s...",
        prompt_outcome.text[:200],
    )

    parse_result = _parse_story_response(prompt_outcome.text)
    _record_story_generation(
        context,
        parse_result,
        stage_label="Initial Story Generation",
        metadata_label="Initial Story Generation (Metadata)",
        fallback_label="Initial Story Generation (Story Content)",
    )

    decision = _evaluate_design_decision(parse_result, story_mode)
    design_response = _activate_design_phase_if_needed(context, decision)
    if design_response:
        return design_response

    suggested_theme = get_theme_suggestion(topic)
    _transition_phase(session_data, StorywritingPhase.WRITING)
    return ChatResponse(
        response=parse_result.story,
        sessionData=session_data,
        suggestedTheme=suggested_theme,
    )


async def _handle_completed_story_flow(
    user_message: str, session_data: SessionData
) -> ChatResponse:
    _transition_phase(session_data, StorywritingPhase.COMPLETED)
    message_lower = user_message.lower().strip()
    if session_data.awaiting_story_confirmation:
        return await _process_story_confirmation_response(
            user_message, message_lower, session_data
        )

    topic_switch_response = await _handle_spontaneous_topic_switch(
        user_message, message_lower, session_data
    )
    if topic_switch_response:
        return topic_switch_response

    return ChatResponse(
        response=content_manager.get_bot_response("story_mode.story_ending"),
        sessionData=session_data,
    )


async def _process_story_confirmation_response(
    user_message: str, message_lower: str, session_data: SessionData
) -> ChatResponse:
    positive_responses = [
        "yes",
        "yeah",
        "yep",
        "sure",
        "ok",
        "okay",
        "i want",
        "let's",
        "space",
        "fantasy",
        "sports",
        "ocean",
        "animals",
        "mystery",
        "adventure",
        "food",
        "creative",
        "magic",
    ]
    negative_responses = [
        "no",
        "nah",
        "not now",
        "maybe later",
        "i'm done",
        "that's it",
        "bye",
    ]

    if any(neg in message_lower for neg in negative_responses):
        session_data.awaiting_story_confirmation = False
        return ChatResponse(
            response=content_manager.get_bot_response("story_mode.session_goodbye"),
            sessionData=session_data,
        )

    if any(pos in message_lower for pos in positive_responses) or len(
        user_message.split()
    ) >= 1:
        potential_new_topic = extract_topic_from_message(user_message)
        _reset_session_for_new_story(session_data, potential_new_topic)
        intro_prefix = (
            f"Great choice! Let's write a {potential_new_topic} story! 🌟"
            if potential_new_topic
            else "Great choice! Let's write a new story! 🌟"
        )
        return await _generate_initial_story_for_topic(
            session_data,
            potential_new_topic,
            intro_prefix=intro_prefix,
        )

    return ChatResponse(
        response=content_manager.get_bot_response("errors.clarification_needed"),
        sessionData=session_data,
    )


async def _handle_spontaneous_topic_switch(
    user_message: str, message_lower: str, session_data: SessionData
) -> Optional[ChatResponse]:
    if not _should_check_for_new_topic(message_lower, user_message):
        return None

    potential_new_topic = extract_topic_from_message(user_message)
    if not potential_new_topic or potential_new_topic == session_data.topic:
        return None

    _reset_session_for_new_story(session_data, potential_new_topic)
    return await _generate_initial_story_for_topic(
        session_data,
        potential_new_topic,
        intro_prefix=None,
    )


# === New Story Flow ===


async def _generate_initial_story_for_topic(
    session_data: SessionData,
    topic: Optional[str],
    *,
    intro_prefix: Optional[str],
) -> ChatResponse:
    context = StoryTurnContext(
        session=session_data,
        topic=topic,
        intro_prefix=intro_prefix,
    )
    plan = PromptPlan(
        prompt=prompt_manager.get_story_opening_prompt(topic, "auto"),
        tags=["story_generation"],
        sub_interaction=1,
    )

    try:
        prompt_outcome = _execute_story_prompt(
            plan,
            context,
            session_data.askedVocabWords,
            apply_vocab=False,
        )
        parse_result = _parse_story_response(prompt_outcome.text)

        total_characters = len(parse_result.structured.entities.characters.named) + len(
            parse_result.structured.entities.characters.unnamed
        )
        total_locations = len(parse_result.structured.entities.locations.named) + len(
            parse_result.structured.entities.locations.unnamed
        )
        logger.info(
            "✅ ENTITY PARSE: Found %s characters, %s locations",
            total_characters,
            total_locations,
        )

        _record_story_generation(
            context,
            parse_result,
            stage_label="New Story Generation",
            metadata_label="New Story Generation (Metadata)",
            fallback_label="New Story Generation (Story Content)",
        )

        decision = _evaluate_design_decision(parse_result, "auto")
        design_response = _activate_design_phase_if_needed(
            context, decision, stage_label="NEW STORY DESIGN"
        )
        if design_response:
            design_response.response = _apply_intro_prefix(
                intro_prefix, parse_result.story
            )
            return design_response

        _transition_phase(session_data, StorywritingPhase.WRITING)
        return ChatResponse(
            response=_apply_intro_prefix(intro_prefix, parse_result.story),
            sessionData=session_data,
            suggestedTheme=get_theme_suggestion(topic),
        )

    except Exception as exc:  # pylint: disable=broad-except
        logger.warning(
            "⚠️ NEW STORY FALLBACK: Story generation failed, using simple generation: %s",
            exc,
        )
        fallback_plan = PromptPlan(
            prompt=prompt_manager.get_topic_selection_story_prompt(topic),
            tags=["story_generation"],
            sub_interaction=1,
        )
        fallback_outcome = _execute_story_prompt(
            fallback_plan,
            context,
            session_data.askedVocabWords,
        )

        context.session.storyParts.append(fallback_outcome.text)
        _extend_content_vocabulary(
            context.session,
            fallback_outcome.selected_vocab,
            "New Story Generation (Fallback)",
        )
        _log_vocabulary_snapshot(
            topic,
            context.session,
            fallback_outcome.text,
            "New Story Generation (Fallback)",
        )

        _transition_phase(session_data, StorywritingPhase.WRITING)
        return ChatResponse(
            response=_apply_intro_prefix(intro_prefix, fallback_outcome.text),
            sessionData=session_data,
            suggestedTheme=get_theme_suggestion(topic),
        )


def _apply_intro_prefix(prefix: Optional[str], story_text: str) -> str:
    if prefix:
        return f"{prefix}\n\n{story_text}"
    return story_text


# === Story Continuation Flow ===


def _capture_user_turn(context: StoryTurnContext) -> None:
    context.session.storyParts.append(f"User: {context.user_message}")


def _maybe_update_story_assessment(context: StoryTurnContext) -> None:
    if context.session.currentStep < 3:
        return

    try:
        assessment_prompt = prompt_manager.get_story_arc_assessment_prompt(
            context.session.storyParts,
            context.topic,
        )
        assessment_response = llm_provider.generate_response(assessment_prompt)
        assessment_duration = get_latest_llm_timing()

        _log_educational_interaction(
            "story_assessment",
            assessment_response,
            assessment_duration,
            context.session,
            context.user_message or "",
            sub_interaction=1,
        )

        assessment = json.loads(assessment_response)

        context.session.narrativeAssessment = assessment
        context.session.storyPhase = assessment.get("current_phase", "development")
        context.session.characterGrowthScore = assessment.get("character_growth", 0)
        context.session.completenessScore = assessment.get("completeness_score", 0)
        context.session.conflictType = assessment.get("conflict_type", "none")

        logger.info(
            "📖 STORY ARC ASSESSMENT: Phase=%s, Growth=%s%, Complete=%s%, Conflict=%s",
            context.session.storyPhase,
            context.session.characterGrowthScore,
            context.session.completenessScore,
            context.session.conflictType,
        )

    except Exception as exc:  # pylint: disable=broad-except
        logger.error(
            "❌ Story assessment failed: %s, falling back to quality gates",
            exc,
        )
        context.session.narrativeAssessment = None


def _build_continuation_plan(
    context: StoryTurnContext, story_context: str
) -> PromptPlan:
    should_end_story, ending_reason = prompt_manager.should_end_story_intelligently(
        context.session
    )
    logger.info(
        "📖 STORY ENDING DECISION: %s, reason: %s",
        should_end_story,
        ending_reason,
    )

    if should_end_story:
        prompt = prompt_manager.get_story_ending_prompt(context.topic, story_context)
        return PromptPlan(
            prompt=prompt,
            tags=["story_ending"],
            should_end_story=True,
            sub_interaction=1,
        )

    if context.session.narrativeAssessment:
        prompt = prompt_manager.get_narrative_continuation_prompt(
            context.session.narrativeAssessment,
            context.topic,
            story_context,
        )
        logger.info(
            "📖 NARRATIVE CONTINUATION: Using phase-aware prompt for %s phase",
            context.session.storyPhase,
        )
    else:
        prompt = prompt_manager.get_continue_story_prompt(context.topic, story_context)
        logger.info(
            "📖 NARRATIVE CONTINUATION: Using standard continuation prompt"
        )

    if (
        context.session.narrativeAssessment
        and not context.session.narrativeAssessment.get("has_clear_conflict", False)
        and context.session.currentStep <= 3
    ):
        conflict_prompt = prompt_manager.get_conflict_integration_prompt(
            context.topic,
            context.session.conflictType,
            context.session.conflictScale,
        )
        prompt = f"{prompt}\n\nADDITIONAL GUIDANCE: {conflict_prompt}"
        logger.info(
            "📖 CONFLICT INTEGRATION: Added conflict guidance for %s story",
            context.topic,
        )

    return PromptPlan(
        prompt=prompt,
        tags=["story_generation"],
        should_end_story=False,
        sub_interaction=2,
    )


async def _handle_story_continuation(
    user_message: str, session_data: SessionData
) -> ChatResponse:
    _transition_phase(session_data, StorywritingPhase.WRITING)

    context = StoryTurnContext(
        session=session_data,
        topic=session_data.topic,
        user_message=user_message,
    )
    _capture_user_turn(context)

    grammar_feedback = llm_provider.provide_grammar_feedback(user_message)
    feedback_duration = get_latest_llm_timing()

    story_context = context.story_window()

    _maybe_update_story_assessment(context)

    plan = _build_continuation_plan(context, story_context)
    outcome = _execute_story_prompt(plan, context, context.vocab_pool)

    stage_label = "Story Ending" if plan.should_end_story else "Story Continuation"
    vocab_log_prefix = (
        "Story ending" if plan.should_end_story else "Story continuation"
    )

    if outcome.selected_vocab:
        logger.info("%s included vocabulary: %s", vocab_log_prefix, outcome.selected_vocab)
        _extend_content_vocabulary(
            session_data,
            outcome.selected_vocab,
            stage_label,
        )

    _log_vocabulary_snapshot(
        session_data.topic,
        session_data,
        outcome.text,
        stage_label,
    )

    if grammar_feedback:
        _log_educational_interaction(
            "grammar_feedback",
            grammar_feedback,
            feedback_duration,
            session_data,
            user_message,
            sub_interaction=1,
        )

    event_type = plan.tags[0] if plan.tags else (
        "story_ending" if plan.should_end_story else "story_generation"
    )
    _log_educational_interaction(
        event_type,
        outcome.text,
        outcome.duration,
        session_data,
        user_message,
        sub_interaction=plan.sub_interaction,
    )

    final_story = _prepend_feedback(grammar_feedback, outcome.text)

    if plan.should_end_story:
        session_data.storyParts.append(final_story)
        session_data.isComplete = True
        _transition_phase(session_data, StorywritingPhase.COMPLETED)
        return ChatResponse(
            response=final_story,
            vocabQuestion=None,
            sessionData=session_data,
        )

    session_data.storyParts.append(final_story)
    session_data.currentStep += 1

    return ChatResponse(response=final_story, sessionData=session_data)


def _reset_session_for_new_story(session_data: SessionData, new_topic: str) -> None:
    _transition_phase(session_data, StorywritingPhase.NOT_STARTED)
    session_data.topic = new_topic
    session_data.storyParts = []
    session_data.currentStep = 2
    session_data.isComplete = False
    session_data.askedVocabWords = []
    session_data.awaiting_story_confirmation = False
    session_data.vocabularyPhase = VocabularyPhase()
    session_data.contentVocabulary = []

    session_data.designPhase = None
    session_data.currentDesignAspect = None
    session_data.designAspectHistory = []
    session_data.designComplete = False
    session_data.namingComplete = False
    session_data.designedEntities = []
    session_data.currentEntityType = None
    session_data.currentEntityDescriptor = None
    session_data.currentEntityName = None
    session_data.entityNameOverrides = {}
    session_data.storyPhase = None
    session_data.conflictType = None
    session_data.conflictScale = None
    session_data.narrativeAssessment = None


def _should_check_for_new_topic(message_lower: str, user_message: str) -> bool:
    generic_responses = [
        "yes",
        "no",
        "ok",
        "okay",
        "sure",
        "thanks",
        "thank you",
        "great",
        "cool",
        "awesome",
        "nice",
    ]
    vocab_related = [
        "what",
        "how",
        "why",
        "when",
        "where",
        "explain",
        "tell me",
        "show me",
    ]

    return (
        len(user_message.split()) >= 2
        and not any(response in message_lower for response in generic_responses)
        and not any(word in message_lower for word in vocab_related)
        and not message_lower.startswith(("i ", "we ", "that ", "this ", "it "))
    )


def _generate_vocabulary_question(
    session_data: SessionData,
    story_text: str,
    log_label: str,
    telemetry_trigger: str,
):
    logger.info("🔍 VOCAB DEBUG - %s vocabulary question processing:", log_label)
    logger.info("  Received askedVocabWords: %s", session_data.askedVocabWords)

    content_vocab_words = extract_vocabulary_from_content(
        story_text, session_data.contentVocabulary
    )
    logger.info("  Content vocabulary words found: %s", content_vocab_words)

    available_words = [
        word for word in content_vocab_words if word not in session_data.askedVocabWords
    ]
    logger.info("  Available words (not asked yet): %s", available_words)

    if available_words:
        selected_word = select_best_vocabulary_word(available_words)
        logger.info("  Selected word: '%s'", selected_word)

        session_data.askedVocabWords.append(selected_word)
        session_data.vocabularyPhase.questionsAsked += 1

        logger.info("  Updated askedVocabWords: %s", session_data.askedVocabWords)
        logger.info(
            "  Updated questionsAsked: %s", session_data.vocabularyPhase.questionsAsked
        )

        vocab_question = llm_provider.generate_vocabulary_question(
            selected_word, context=story_text
        )
        vocab_duration = get_latest_llm_timing()
        logger.info(
            "  Generated question: '%s'",
            vocab_question.get("question", "N/A"),
        )

        return vocab_question, vocab_duration, telemetry_trigger

    vocab_word_data = vocabulary_manager.select_vocabulary_word(
        topic=session_data.topic or "general",
        used_words=session_data.askedVocabWords,
    )

    if vocab_word_data:
        logger.info("  Selected curated word: '%s'", vocab_word_data["word"])

        session_data.askedVocabWords.append(vocab_word_data["word"])
        session_data.vocabularyPhase.questionsAsked += 1

        logger.info("  Updated askedVocabWords: %s", session_data.askedVocabWords)
        logger.info(
            "  Updated questionsAsked: %s", session_data.vocabularyPhase.questionsAsked
        )

        vocab_question = llm_provider.generate_vocabulary_question(
            vocab_word_data["word"], context=vocab_word_data["definition"]
        )
        vocab_duration = get_latest_llm_timing()
        logger.info(
            "  Generated question: '%s'",
            vocab_question.get("question", "N/A"),
        )

        return vocab_question, vocab_duration, f"{telemetry_trigger}_fallback"

    return None

async def handle_start_vocabulary(session_data: SessionData) -> ChatResponse:
    """Start vocabulary phase after story completion"""
    logger.info("Starting vocabulary phase")

    _transition_phase(session_data, StorywritingPhase.VOCABULARY)
    session_data.vocabularyPhase.questionsAsked = 0
    session_data.vocabularyPhase.isComplete = False

    all_story_text = "\n".join(session_data.storyParts)
    question_result = _generate_vocabulary_question(
        session_data,
        all_story_text,
        log_label="First",
        telemetry_trigger="start_vocabulary",
    )

    if question_result:
        vocab_question, vocab_duration, telemetry_trigger = question_result

        vocab_educational_data = collect_educational_data(
            session_data,
            ChatResponse(response=json.dumps(vocab_question)),
            "storywriting",
            telemetry_trigger,
            ["vocabulary_question"],
            sub_interaction=1,
        )
        latency_logger.log_educational_interaction(
            "vocabulary_question",
            json.dumps(vocab_question),
            vocab_duration,
            vocab_educational_data,
        )

        return ChatResponse(
            response=content_manager.get_bot_response("vocabulary.intro_after_story"),
            vocabQuestion=VocabQuestion(**vocab_question),
            sessionData=session_data,
        )

    return await handle_finish_vocabulary(session_data)

async def handle_next_vocabulary(session_data: SessionData) -> ChatResponse:
    """Request next vocabulary question with count validation"""
    logger.info(f"Requesting vocabulary question {session_data.vocabularyPhase.questionsAsked + 1} of {session_data.vocabularyPhase.maxQuestions}")
    
    _transition_phase(session_data, StorywritingPhase.VOCABULARY)
    # Check if we've reached the maximum
    if session_data.vocabularyPhase.questionsAsked >= session_data.vocabularyPhase.maxQuestions:
        logger.info("Max vocabulary questions reached, finishing vocabulary phase")
        return await handle_finish_vocabulary(session_data)
    
    all_story_text = "\n".join(session_data.storyParts)
    question_result = _generate_vocabulary_question(
        session_data,
        all_story_text,
        log_label="Next",
        telemetry_trigger="next_vocabulary",
    )

    if question_result:
        vocab_question, vocab_duration, telemetry_trigger = question_result

        vocab_educational_data = collect_educational_data(
            session_data,
            ChatResponse(response=json.dumps(vocab_question)),
            "storywriting",
            telemetry_trigger,
            ["vocabulary_question"],
            sub_interaction=1,
        )
        latency_logger.log_educational_interaction(
            "vocabulary_question",
            json.dumps(vocab_question),
            vocab_duration,
            vocab_educational_data,
        )

        return ChatResponse(
            response=content_manager.get_bot_response("vocabulary.next_question"),
            vocabQuestion=VocabQuestion(**vocab_question),
            sessionData=session_data,
        )

    logger.info("No more vocabulary words available, finishing vocabulary phase")
    return await handle_finish_vocabulary(session_data)

async def handle_finish_vocabulary(session_data: SessionData) -> ChatResponse:
    """Finish vocabulary phase and ask for new story"""
    logger.info("Finishing vocabulary phase, asking for new story")

    # Mark vocabulary phase as complete
    session_data.vocabularyPhase.isComplete = True
    session_data.awaiting_story_confirmation = True
    _transition_phase(session_data, StorywritingPhase.COMPLETED)

    story_completion_prompt = prompt_manager.get_story_completion_prompt()

    return ChatResponse(
        response=story_completion_prompt,
        sessionData=session_data
    )


__all__ = [
    "handle_finish_vocabulary",
    "handle_next_vocabulary",
    "handle_start_vocabulary",
    "handle_storywriting",
]

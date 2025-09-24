"""Storywriting service handling multi-step story interactions."""

import json
import logging

from content_manager import content_manager
from llm_provider import llm_provider
from prompt_manager import prompt_manager
from telemetry import collect_educational_data, get_latest_llm_timing, latency_logger
from utils import extract_topic_from_message, get_theme_suggestion
from vocabulary_manager import vocabulary_manager

from .design_service import (
    handle_design_phase_interaction,
    should_trigger_design_phase,
    trigger_design_phase,
    trigger_enhanced_design_phase,
    validate_entity_structure,
)
from .story_parser import parse_enhanced_story_response, parse_structured_story_response
from .vocabulary_service import (
    extract_vocabulary_from_content,
    log_vocabulary_debug_info,
    select_best_vocabulary_word,
)
from models.schemas import ChatResponse, SessionData, VocabQuestion, VocabularyPhase


logger = logging.getLogger(__name__)


async def handle_storywriting(user_message: str, session_data: SessionData, story_mode: str = "auto") -> ChatResponse:
    """Orchestrate storywriting interactions across the different phases."""
    return await _dispatch_story_phase(user_message, session_data, story_mode)


async def _dispatch_story_phase(
    user_message: str, session_data: SessionData, story_mode: str
) -> ChatResponse:
    vocabulary_response = await _handle_vocabulary_command(user_message, session_data)
    if vocabulary_response:
        return vocabulary_response

    if _is_design_phase_active(session_data):
        return await handle_design_phase_interaction(user_message, session_data)

    if not session_data.topic:
        return await _handle_topic_initialization(user_message, session_data, story_mode)

    if session_data.isComplete:
        return await _handle_completed_story_flow(user_message, session_data)

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
        return await handler(session_data)

    return None


def _is_design_phase_active(session_data: SessionData) -> bool:
    return bool(session_data.designPhase and not session_data.designComplete)


async def _handle_topic_initialization(
    user_message: str, session_data: SessionData, story_mode: str
) -> ChatResponse:
    topic = extract_topic_from_message(user_message)
    session_data.topic = topic
    session_data.currentStep = 2  # Moving to Step 2 after topic selection

    structured_prompt = prompt_manager.get_story_opening_prompt(topic, story_mode)
    enhanced_prompt, _ = prompt_manager.enhance_with_vocabulary(
        structured_prompt, topic, session_data.askedVocabWords
    )

    raw_response = llm_provider.generate_response(enhanced_prompt)
    logger.info(
        "🎯 LLM RESPONSE DEBUG: Raw response length: %s characters",
        len(raw_response),
    )
    logger.info("🎯 LLM RESPONSE DEBUG: First 200 chars: %s...", raw_response[:200])

    try:
        enhanced_response = parse_enhanced_story_response(raw_response)
        logger.info("✅ ENTITY PARSE: Successfully parsed with new entity system")
        logger.info(
            "🎯 ENTITY DEBUG: Characters named=%s, unnamed=%s",
            enhanced_response.entities.characters.named,
            enhanced_response.entities.characters.unnamed,
        )
        logger.info(
            "🎯 ENTITY DEBUG: Locations named=%s, unnamed=%s",
            enhanced_response.entities.locations.named,
            enhanced_response.entities.locations.unnamed,
        )
        logger.info(
            "🎯 ENTITY DEBUG: Vocabulary words: %s",
            enhanced_response.vocabulary_words,
        )

        session_data.storyParts.append(enhanced_response.story)

        if enhanced_response.vocabulary_words:
            session_data.contentVocabulary.extend(enhanced_response.vocabulary_words)
            logger.info(
                "📋 VOCABULARY TRACKING: Added %s words from entity metadata. Total tracked: %s",
                len(enhanced_response.vocabulary_words),
                len(session_data.contentVocabulary),
            )
        else:
            story_vocab_words = extract_vocabulary_from_content(
                enhanced_response.story, session_data.contentVocabulary
            )
            if story_vocab_words:
                session_data.contentVocabulary.extend(story_vocab_words)
                logger.info(
                    "📋 VOCABULARY TRACKING: Fallback - Added %s words from story content. Total tracked: %s",
                    len(story_vocab_words),
                    len(session_data.contentVocabulary),
                )

        log_vocabulary_debug_info(
            topic,
            session_data.askedVocabWords,
            enhanced_response.story,
            "Initial Story Generation",
            len(session_data.contentVocabulary),
        )

        should_trigger = validate_entity_structure(enhanced_response.entities)
        logger.info(
            "🎯 DESIGN PHASE DEBUG: validate_entity_structure() returned: %s",
            should_trigger,
        )

        structured_response = enhanced_response

    except Exception as e:  # pylint: disable=broad-except
        logger.warning(
            "⚠️ FALLBACK: Enhanced parsing failed, using legacy parser: %s",
            e,
        )
        structured_response = parse_structured_story_response(raw_response)
        logger.info(
            "🎯 LEGACY DEBUG: Parsed metadata: %s",
            structured_response.metadata,
        )
        logger.info(
            "🎯 LEGACY DEBUG: design_options: %s",
            structured_response.metadata.design_options,
        )

        session_data.storyParts.append(structured_response.story)

        story_vocab_words = extract_vocabulary_from_content(
            structured_response.story, session_data.contentVocabulary
        )
        if story_vocab_words:
            session_data.contentVocabulary.extend(story_vocab_words)
            logger.info(
                "📋 VOCABULARY TRACKING: Legacy - Added %s words from story content. Total tracked: %s",
                len(story_vocab_words),
                len(session_data.contentVocabulary),
            )

        log_vocabulary_debug_info(
            topic,
            session_data.askedVocabWords,
            structured_response.story,
            "Initial Story Generation",
            len(session_data.contentVocabulary),
        )

        should_trigger = should_trigger_design_phase(structured_response)
        logger.info(
            "🎯 DESIGN PHASE DEBUG: Legacy should_trigger_design_phase() returned: %s",
            should_trigger,
        )

    if should_trigger:
        if hasattr(structured_response, "entities"):
            total_entities = (
                len(structured_response.entities.characters.unnamed)
                + len(structured_response.entities.locations.unnamed)
            )
            logger.info(
                "✅ DESIGN PHASE: Triggering design phase with %s designable entities",
                total_entities,
            )
            design_response = trigger_enhanced_design_phase(
                session_data, structured_response
            )
        else:
            logger.info(
                "✅ DESIGN PHASE: Triggering legacy design phase with options: %s",
                structured_response.metadata.design_options,
            )
            design_response = trigger_design_phase(session_data, structured_response)

        design_response.suggestedTheme = get_theme_suggestion(topic)
        return design_response

    logger.info(
        "❌ DESIGN PHASE: Skipping design phase - no designable entities found"
    )
    suggested_theme = get_theme_suggestion(topic)

    return ChatResponse(
        response=structured_response.story,
        sessionData=session_data,
        suggestedTheme=suggested_theme,
    )


async def _handle_completed_story_flow(
    user_message: str, session_data: SessionData
) -> ChatResponse:
    message_lower = user_message.lower().strip()

    if session_data.awaiting_story_confirmation:
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
                response=content_manager.get_bot_response(
                    "story_mode.session_goodbye"
                ),
                sessionData=session_data,
            )

        if any(pos in message_lower for pos in positive_responses) or len(
            user_message.split()
        ) >= 1:
            potential_new_topic = extract_topic_from_message(user_message)
            _reset_session_for_new_story(session_data, potential_new_topic)

            story_prompt = prompt_manager.get_story_opening_prompt(
                potential_new_topic, "auto"
            )
            raw_response = llm_provider.generate_response(story_prompt)

            try:
                enhanced_response = parse_enhanced_story_response(raw_response)
                logger.info(
                    "✅ ENTITY PARSE: Found %s characters, %s locations",
                    len(
                        enhanced_response.entities.characters.named
                        + enhanced_response.entities.characters.unnamed
                    ),
                    len(
                        enhanced_response.entities.locations.named
                        + enhanced_response.entities.locations.unnamed
                    ),
                )

                session_data.storyParts.append(enhanced_response.story)

                if enhanced_response.vocabulary_words:
                    session_data.contentVocabulary.extend(
                        enhanced_response.vocabulary_words
                    )
                    logger.info(
                        "📋 VOCABULARY TRACKING: Enhanced - Added %s words from entity metadata. Total tracked: %s",
                        len(enhanced_response.vocabulary_words),
                        len(session_data.contentVocabulary),
                    )

                log_vocabulary_debug_info(
                    potential_new_topic,
                    session_data.askedVocabWords,
                    enhanced_response.story,
                    "New Story Generation",
                    len(session_data.contentVocabulary),
                )

                should_trigger = validate_entity_structure(
                    enhanced_response.entities
                )
                logger.info(
                    "🎯 NEW STORY DESIGN: validate_entity_structure() returned: %s",
                    should_trigger,
                )

                if should_trigger:
                    total_entities = (
                        len(enhanced_response.entities.characters.unnamed)
                        + len(enhanced_response.entities.locations.unnamed)
                    )
                    logger.info(
                        "✅ NEW STORY DESIGN: Triggering design phase with %s designable entities",
                        total_entities,
                    )
                    design_response = trigger_enhanced_design_phase(
                        session_data, enhanced_response
                    )
                    design_response.response = (
                        f"Great choice! Let's write a {potential_new_topic} story! 🌟\n\n"
                        f"{enhanced_response.story}"
                    )
                    design_response.suggestedTheme = get_theme_suggestion(
                        potential_new_topic
                    )
                    return design_response

                logger.info(
                    "❌ NEW STORY DESIGN: Skipping design phase - no designable entities found"
                )
                return ChatResponse(
                    response=(
                        f"Great choice! Let's write a {potential_new_topic} story! 🌟\n\n"
                        f"{enhanced_response.story}"
                    ),
                    sessionData=session_data,
                    suggestedTheme=get_theme_suggestion(potential_new_topic),
                )

            except Exception as e:  # pylint: disable=broad-except
                logger.warning(
                    "⚠️ NEW STORY FALLBACK: Enhanced parsing failed, using simple generation: %s",
                    e,
                )
                base_prompt = prompt_manager.get_topic_selection_story_prompt(
                    potential_new_topic
                )
                enhanced_prompt, selected_vocab = prompt_manager.enhance_with_vocabulary(
                    base_prompt, potential_new_topic, session_data.askedVocabWords
                )
                story_response = llm_provider.generate_response(enhanced_prompt)
                session_data.storyParts.append(story_response)

                if selected_vocab:
                    session_data.contentVocabulary.extend(selected_vocab)
                    logger.info(
                        "📋 VOCABULARY TRACKING: Fallback - Added %s words. Total: %s",
                        len(selected_vocab),
                        len(session_data.contentVocabulary),
                    )

                log_vocabulary_debug_info(
                    potential_new_topic,
                    session_data.askedVocabWords,
                    story_response,
                    "New Story Generation (Fallback)",
                    len(session_data.contentVocabulary),
                )

                return ChatResponse(
                    response=(
                        f"Great choice! Let's write a {potential_new_topic} story! 🌟\n\n"
                        f"{story_response}"
                    ),
                    sessionData=session_data,
                    suggestedTheme=get_theme_suggestion(potential_new_topic),
                )

        return ChatResponse(
            response=content_manager.get_bot_response("errors.clarification_needed"),
            sessionData=session_data,
        )

    if _should_check_for_new_topic(message_lower, user_message):
        potential_new_topic = extract_topic_from_message(user_message)
        if potential_new_topic and potential_new_topic != session_data.topic:
            _reset_session_for_new_story(session_data, potential_new_topic)

            story_prompt = prompt_manager.get_story_opening_prompt(
                potential_new_topic, "auto"
            )
            raw_response = llm_provider.generate_response(story_prompt)

            try:
                enhanced_response = parse_enhanced_story_response(raw_response)
                logger.info(
                    "✅ TOPIC SWITCH PARSE: Found %s characters, %s locations",
                    len(
                        enhanced_response.entities.characters.named
                        + enhanced_response.entities.characters.unnamed
                    ),
                    len(
                        enhanced_response.entities.locations.named
                        + enhanced_response.entities.locations.unnamed
                    ),
                )

                session_data.storyParts.append(enhanced_response.story)

                if enhanced_response.vocabulary_words:
                    session_data.contentVocabulary.extend(
                        enhanced_response.vocabulary_words
                    )
                    logger.info(
                        "📋 VOCABULARY TRACKING: Topic Switch Enhanced - Added %s words from entity metadata. Total tracked: %s",
                        len(enhanced_response.vocabulary_words),
                        len(session_data.contentVocabulary),
                    )

                log_vocabulary_debug_info(
                    potential_new_topic,
                    session_data.askedVocabWords,
                    enhanced_response.story,
                    "Story Topic Switch",
                    len(session_data.contentVocabulary),
                )

                should_trigger = validate_entity_structure(
                    enhanced_response.entities
                )
                logger.info(
                    "🎯 TOPIC SWITCH DESIGN: validate_entity_structure() returned: %s",
                    should_trigger,
                )

                if should_trigger:
                    total_entities = (
                        len(enhanced_response.entities.characters.unnamed)
                        + len(enhanced_response.entities.locations.unnamed)
                    )
                    logger.info(
                        "✅ TOPIC SWITCH DESIGN: Triggering design phase with %s designable entities",
                        total_entities,
                    )
                    design_response = trigger_enhanced_design_phase(
                        session_data, enhanced_response
                    )
                    design_response.response = enhanced_response.story
                    design_response.suggestedTheme = get_theme_suggestion(
                        potential_new_topic
                    )
                    return design_response

                logger.info(
                    "❌ TOPIC SWITCH DESIGN: Skipping design phase - no designable entities found"
                )
                return ChatResponse(
                    response=enhanced_response.story,
                    sessionData=session_data,
                    suggestedTheme=get_theme_suggestion(potential_new_topic),
                )

            except Exception as e:  # pylint: disable=broad-except
                logger.warning(
                    "⚠️ TOPIC SWITCH FALLBACK: Enhanced parsing failed, using simple generation: %s",
                    e,
                )
                base_prompt = prompt_manager.get_topic_selection_story_prompt(
                    potential_new_topic
                )
                enhanced_prompt, selected_vocab = prompt_manager.enhance_with_vocabulary(
                    base_prompt, potential_new_topic, session_data.askedVocabWords
                )
                story_response = llm_provider.generate_response(enhanced_prompt)
                session_data.storyParts.append(story_response)

                if selected_vocab:
                    session_data.contentVocabulary.extend(selected_vocab)
                    logger.info(
                        "📋 VOCABULARY TRACKING: Topic Switch Fallback - Added %s words. Total: %s",
                        len(selected_vocab),
                        len(session_data.contentVocabulary),
                    )

                log_vocabulary_debug_info(
                    potential_new_topic,
                    session_data.askedVocabWords,
                    story_response,
                    "Story Topic Switch (Fallback)",
                    len(session_data.contentVocabulary),
                )

                return ChatResponse(
                    response=story_response,
                    sessionData=session_data,
                    suggestedTheme=get_theme_suggestion(potential_new_topic),
                )

    return ChatResponse(
        response=content_manager.get_bot_response("story_mode.story_ending"),
        sessionData=session_data,
    )


async def _handle_story_continuation(
    user_message: str, session_data: SessionData
) -> ChatResponse:
    session_data.storyParts.append(f"User: {user_message}")

    grammar_feedback = llm_provider.provide_grammar_feedback(user_message)
    feedback_duration = get_latest_llm_timing()

    story_context = "\n".join(session_data.storyParts[-3:])

    if session_data.currentStep >= 3:
        try:
            assessment_prompt = prompt_manager.get_story_arc_assessment_prompt(
                session_data.storyParts, session_data.topic
            )
            assessment_response = llm_provider.generate_response(assessment_prompt)
            assessment_duration = get_latest_llm_timing()

            assessment_educational_data = collect_educational_data(
                session_data,
                ChatResponse(response=assessment_response),
                "storywriting",
                user_message,
                ["story_assessment"],
                sub_interaction=1,
            )
            latency_logger.log_educational_interaction(
                "story_assessment",
                assessment_response,
                assessment_duration,
                assessment_educational_data,
            )

            assessment = json.loads(assessment_response)

            session_data.narrativeAssessment = assessment
            session_data.storyPhase = assessment.get("current_phase", "development")
            session_data.characterGrowthScore = assessment.get("character_growth", 0)
            session_data.completenessScore = assessment.get("completeness_score", 0)
            session_data.conflictType = assessment.get("conflict_type", "none")

            logger.info(
                "📖 STORY ARC ASSESSMENT: Phase=%s, Growth=%s%, Complete=%s%, Conflict=%s",
                session_data.storyPhase,
                session_data.characterGrowthScore,
                session_data.completenessScore,
                session_data.conflictType,
            )

        except Exception as e:  # pylint: disable=broad-except
            logger.error("❌ Story assessment failed: %s, falling back to quality gates", e)
            session_data.narrativeAssessment = None

    should_end_story, ending_reason = prompt_manager.should_end_story_intelligently(
        session_data
    )
    logger.info(
        "📖 STORY ENDING DECISION: %s, reason: %s",
        should_end_story,
        ending_reason,
    )

    if should_end_story:
        base_prompt = prompt_manager.get_story_ending_prompt(
            session_data.topic, story_context
        )
        enhanced_prompt, selected_vocab = prompt_manager.enhance_with_vocabulary(
            base_prompt,
            session_data.topic,
            session_data.askedVocabWords + session_data.contentVocabulary,
        )
        story_response = llm_provider.generate_response(enhanced_prompt)
        story_duration = get_latest_llm_timing()

        ending_educational_data = collect_educational_data(
            session_data,
            ChatResponse(response=story_response),
            "storywriting",
            user_message,
            ["story_ending"],
            sub_interaction=1,
        )
        latency_logger.log_educational_interaction(
            "story_ending",
            story_response,
            story_duration,
            ending_educational_data,
        )

        if selected_vocab:
            logger.info("Story ending included vocabulary: %s", selected_vocab)
            session_data.contentVocabulary.extend(selected_vocab)
            logger.info(
                "📋 VOCABULARY TRACKING: Added %s words to session. Total tracked: %s",
                len(selected_vocab),
                len(session_data.contentVocabulary),
            )

        log_vocabulary_debug_info(
            session_data.topic,
            session_data.askedVocabWords + session_data.contentVocabulary,
            story_response,
            "Story Ending",
            len(session_data.contentVocabulary),
        )

        if grammar_feedback:
            story_response = grammar_feedback + "\n\n" + story_response

        session_data.storyParts.append(story_response)
        session_data.isComplete = True

        return ChatResponse(
            response=story_response,
            vocabQuestion=None,
            sessionData=session_data,
        )

    if session_data.narrativeAssessment:
        base_prompt = prompt_manager.get_narrative_continuation_prompt(
            session_data.narrativeAssessment, session_data.topic, story_context
        )
        logger.info(
            "📖 NARRATIVE CONTINUATION: Using phase-aware prompt for %s phase",
            session_data.storyPhase,
        )
    else:
        base_prompt = prompt_manager.get_continue_story_prompt(
            session_data.topic, story_context
        )
        logger.info(
            "📖 NARRATIVE CONTINUATION: Using standard continuation prompt"
        )

    if (
        session_data.narrativeAssessment
        and not session_data.narrativeAssessment.get("has_clear_conflict", False)
        and session_data.currentStep <= 3
    ):
        conflict_prompt = prompt_manager.get_conflict_integration_prompt(
            session_data.topic, session_data.conflictType, session_data.conflictScale
        )
        base_prompt += f"\n\nADDITIONAL GUIDANCE: {conflict_prompt}"
        logger.info(
            "📖 CONFLICT INTEGRATION: Added conflict guidance for %s story",
            session_data.topic,
        )

    enhanced_prompt, selected_vocab = prompt_manager.enhance_with_vocabulary(
        base_prompt,
        session_data.topic,
        session_data.askedVocabWords + session_data.contentVocabulary,
    )
    story_response = llm_provider.generate_response(enhanced_prompt)
    story_duration = get_latest_llm_timing()

    if selected_vocab:
        logger.info("Story continuation included vocabulary: %s", selected_vocab)
        session_data.contentVocabulary.extend(selected_vocab)
        logger.info(
            "📋 VOCABULARY TRACKING: Added %s words to session. Total tracked: %s",
            len(selected_vocab),
            len(session_data.contentVocabulary),
        )

    log_vocabulary_debug_info(
        session_data.topic,
        session_data.askedVocabWords + session_data.contentVocabulary,
        story_response,
        "Story Continuation",
        len(session_data.contentVocabulary),
    )

    if grammar_feedback:
        feedback_educational_data = collect_educational_data(
            session_data,
            ChatResponse(response=grammar_feedback),
            "storywriting",
            user_message,
            ["grammar_feedback"],
            sub_interaction=1,
        )
        latency_logger.log_educational_interaction(
            "grammar_feedback",
            grammar_feedback,
            feedback_duration,
            feedback_educational_data,
        )

    story_educational_data = collect_educational_data(
        session_data,
        ChatResponse(response=story_response),
        "storywriting",
        user_message,
        ["story_generation"],
        sub_interaction=2,
    )
    latency_logger.log_educational_interaction(
        "story_generation",
        story_response,
        story_duration,
        story_educational_data,
    )

    if grammar_feedback:
        story_response = grammar_feedback + "\n\n" + story_response

    session_data.storyParts.append(story_response)
    session_data.currentStep += 1

    return ChatResponse(response=story_response, sessionData=session_data)


def _reset_session_for_new_story(session_data: SessionData, new_topic: str) -> None:
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
    session_data.storyMetadata = None
    session_data.designComplete = False
    session_data.namingComplete = False
    session_data.designedEntities = []
    session_data.currentEntityType = None
    session_data.currentEntityDescriptor = None
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

    session_data.vocabularyPhase.isActive = True
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
    session_data.vocabularyPhase.isActive = False
    session_data.awaiting_story_confirmation = True
    
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

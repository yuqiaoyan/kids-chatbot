"""Design-phase helpers for storywriting interactions."""

import logging
import random
from typing import List, Optional, Tuple

from content_manager import content_manager
from llm_provider import llm_provider
from prompt_manager import prompt_manager
from telemetry import collect_educational_data, get_latest_llm_timing, latency_logger
from .vocabulary_service import extract_vocabulary_from_content
from models.schemas import (
    ChatResponse,
    DesignPrompt,
    EnhancedStoryResponse,
    SessionData,
    StoryEntities,
)

logger = logging.getLogger(__name__)


def validate_entity_structure(entities: StoryEntities) -> bool:
    """Validate that entity structure has at least one designable character entity."""
    total_character_entities = len(entities.characters.named) + len(entities.characters.unnamed)

    if total_character_entities == 0:
        logging.warning("⚠️ VALIDATION: No CHARACTER entities found for design phase (location design disabled)")
        return False

    named_characters = len(entities.characters.named)
    unnamed_characters = len(entities.characters.unnamed)

    if total_character_entities == 0:
        logging.info("ℹ️ VALIDATION: No CHARACTER entities found - no design phase needed (location design disabled)")
        return False

    if unnamed_characters > 0:
        logging.info(
            "✅ VALIDATION: Found %s unnamed CHARACTERS for full design phase (naming + aspects)",
            unnamed_characters,
        )
    if named_characters > 0:
        logging.info(
            "✅ VALIDATION: Found %s named CHARACTERS for aspect design phase (skip naming)",
            named_characters,
        )

    logging.info(
        "✅ VALIDATION: Total %s CHARACTER entities available for design phase (location design disabled)",
        total_character_entities,
    )
    return True


def get_next_design_entity(
    entities: StoryEntities, designed_entities: Optional[List[str]] = None
) -> Optional[Tuple[str, str, bool]]:
    """Get the next entity that needs design from entity lists."""
    designed_entities = designed_entities or []

    for char in entities.characters.named:
        if char not in designed_entities:
            logging.info("🎯 DESIGN: Next entity is named character '%s' (aspect design only)", char)
            return ("character", char, True)

    for char in entities.characters.unnamed:
        if char not in designed_entities:
            logging.info("🎯 DESIGN: Next entity is unnamed character '%s' (full design)", char)
            return ("character", char, False)

    logging.info("✅ DESIGN: All entities have been designed")
    return None


def load_design_aspects(design_type: str) -> dict:
    """Load design aspects from the content manager."""
    try:
        design_templates = content_manager.content.get("design_templates", {})
        aspects = design_templates.get(design_type, {})

        if not aspects:
            logging.warning("No design aspects found for type: %s", design_type)
            return {}

        logging.info("✅ Loaded design aspects for %s from ContentManager", design_type)
        return aspects

    except Exception as exc:  # pylint: disable=broad-except
        logging.error("❌ Failed to load design aspects for %s: %s", design_type, exc)
        return {}


def resolve_entity_name(session_data: SessionData) -> str:
    """Resolve the display name for the current entity."""
    descriptor = session_data.currentEntityDescriptor or "the entity"
    overrides = session_data.entityNameOverrides or {}

    if descriptor in overrides and overrides[descriptor]:
        return overrides[descriptor]

    if session_data.currentEntityName:
        return session_data.currentEntityName

    if descriptor and len(descriptor.split()) == 1 and descriptor[0].isupper():
        return descriptor

    if session_data.currentEntityType == "character":
        return descriptor or "our character"
    if session_data.currentEntityType == "location":
        return descriptor or "this place"
    return descriptor or "the entity"


def finalize_current_entity(session_data: SessionData) -> None:
    """Mark the current entity as fully designed and reset session state."""
    descriptor = session_data.currentEntityDescriptor
    if descriptor:
        if descriptor not in session_data.designedEntities:
            session_data.designedEntities.append(descriptor)
        if session_data.currentEntityName:
            session_data.entityNameOverrides.setdefault(
                descriptor, session_data.currentEntityName
            )

    session_data.designComplete = True
    session_data.designPhase = None
    session_data.currentDesignAspect = None
    session_data.currentEntityType = None
    session_data.currentEntityDescriptor = None
    session_data.currentEntityName = None
    session_data.namingComplete = False


def get_next_design_aspect(design_type: str, used_aspects: List[str]) -> str:
    """Get next design aspect using rotation logic."""
    aspects = load_design_aspects(design_type)
    if not aspects:
        return "appearance"

    available_aspects = [aspect for aspect in aspects if aspect not in used_aspects]
    if not available_aspects:
        available_aspects = list(aspects.keys())

    selected_aspect = random.choice(available_aspects)
    logger.info("🎲 ASPECT SELECTION: Randomly selected '%s' from %s", selected_aspect, available_aspects)
    return selected_aspect

def trigger_enhanced_design_phase(
    session_data: SessionData, enhanced_response: EnhancedStoryResponse
) -> ChatResponse:
    """Initialize design phase using the entity-based metadata system."""
    entities = enhanced_response.entities

    if not hasattr(session_data, "designedEntities"):
        session_data.designedEntities = []
    if session_data.entityNameOverrides is None:
        session_data.entityNameOverrides = {}

    session_data.currentDesignAspect = None
    session_data.designAspectHistory = []
    session_data.designComplete = False
    session_data.namingComplete = False

    next_entity = get_next_design_entity(entities, session_data.designedEntities)
    if not next_entity:
        logging.info("✅ ENHANCED DESIGN: All entities designed, continuing with story")
        return ChatResponse(
            response=enhanced_response.story,
            sessionData=session_data,
        )

    entity_type, entity_descriptor, is_named = next_entity
    session_data.currentEntityType = entity_type
    session_data.currentEntityDescriptor = entity_descriptor
    session_data.currentEntityName = entity_descriptor if is_named else None
    session_data.designPhase = entity_type

    if is_named:
        session_data.namingComplete = True
        aspects = ["appearance", "personality", "dreams", "skills"]
        session_data.currentDesignAspect = random.choice(aspects)
        session_data.entityNameOverrides.setdefault(entity_descriptor, entity_descriptor)
        logging.info(
            "🎯 ENHANCED DESIGN: Named entity '%s' - starting with %s aspect",
            entity_descriptor,
            session_data.currentDesignAspect,
        )
    else:
        session_data.currentDesignAspect = "naming"
        logging.info("🎯 ENHANCED DESIGN: Unnamed entity '%s' - starting with naming", entity_descriptor)

    logging.info("🎯 ENHANCED DESIGN: Starting design for %s '%s'", entity_type, entity_descriptor)

    design_response = create_design_prompt(session_data)
    design_response.response = enhanced_response.story
    return design_response


def create_design_prompt(session_data: SessionData) -> ChatResponse:
    """Create design prompt using enhanced entity system."""
    entity_type = session_data.currentEntityType or "character"
    entity_descriptor = session_data.currentEntityDescriptor or (
        "this place" if entity_type == "location" else "our character"
    )
    current_aspect = session_data.currentDesignAspect or "appearance"

    if current_aspect == "naming":
        try:
            content_manager.get_design_template(f"{entity_type}.naming")
        except Exception as exc:  # pylint: disable=broad-except
            logging.debug("Naming template lookup failed for %s: %s", entity_type, exc)

        name_prompt = f"Can you name {entity_descriptor}?"
        placeholder = f"Enter a name for {entity_descriptor}"
        if entity_type == "character":
            suggestions = [
                "Alex",
                "Maya",
                "Sam",
                "River",
                "Sky",
                "Sage",
                "Blake",
                "Quinn",
            ]
        else:
            suggestions = [
                "Crystal Palace",
                "Mystic Falls",
                "Adventure Park",
                "Sunset Beach",
                "Secret Garden",
                "Magic Library",
                "Wonder Cave",
                "Star Station",
            ]

        design_prompt = DesignPrompt(
            type=entity_type,
            subject_name=entity_descriptor,
            aspect="naming",
            prompt_text=name_prompt,
            suggested_words=suggestions,
            input_placeholder=placeholder,
        )

        return ChatResponse(response="", sessionData=session_data, designPrompt=design_prompt)

    entity_name = resolve_entity_name(session_data)

    design_templates = content_manager.content.get("design_templates", {})
    entity_templates = design_templates.get(entity_type, {})
    aspect_data = entity_templates.get(current_aspect, {})

    prompt_template = aspect_data.get("prompt_template", "Tell us more about {name}!")
    placeholder = aspect_data.get("placeholder", "Write 1-2 sentences")
    suggestions = aspect_data.get(
        "suggestions",
        [
            "brave",
            "curious",
            "kind",
            "imaginative",
            "talented",
            "friendly",
            "helpful",
            "creative",
        ],
    )

    prompt_text = prompt_template.format(name=entity_name)

    design_prompt = DesignPrompt(
        type=session_data.designPhase,
        subject_name=entity_name,
        aspect=current_aspect,
        prompt_text=prompt_text,
        suggested_words=suggestions,
        input_placeholder=placeholder,
    )

    return ChatResponse(response="", sessionData=session_data, designPrompt=design_prompt)


async def handle_design_phase_interaction(
    user_message: str, session_data: SessionData
) -> ChatResponse:
    """Handle user input during design phase."""

    if not session_data.currentEntityType or not session_data.currentEntityDescriptor:
        logging.error("Design interaction received without active entity context")
        return ChatResponse(
            response=content_manager.get_bot_response("story_mode.continue_story"),
            sessionData=session_data,
        )

    entity_type = session_data.currentEntityType
    message_text = user_message.strip()

    if session_data.currentDesignAspect == "naming":
        provided_name = message_text or resolve_entity_name(session_data)

        session_data.entityNameOverrides[session_data.currentEntityDescriptor] = provided_name
        session_data.currentEntityName = provided_name
        session_data.namingComplete = True
        if "naming" not in session_data.designAspectHistory:
            session_data.designAspectHistory.append("naming")

        feedback_response = content_manager.get_bot_response(
            "design_phase.naming_feedback",
            provided_name=provided_name,
            design_phase=entity_type,
        )

        aspects = load_design_aspects(entity_type)
        available_aspects = [
            aspect
            for aspect in aspects.keys()
            if aspect != "naming" and aspect not in session_data.designAspectHistory
        ]

        if available_aspects and len(session_data.designAspectHistory) < 2:
            session_data.currentDesignAspect = random.choice(available_aspects)
            transition_message = (
                f"Now let's bring {provided_name} to life with more details!"
            )
            design_response = create_design_prompt(session_data)
            design_response.response = f"{feedback_response}\n\n{transition_message}"
            return design_response

        finalize_current_entity(session_data)
        design_completion = content_manager.get_bot_response(
            "design_phase.design_completion_simple",
            feedback_response=feedback_response,
            subject_name=provided_name,
        )
        return ChatResponse(response=design_completion, sessionData=session_data)

    # Handle follow-up design aspects
    entity_name = resolve_entity_name(session_data)
    provided_description = message_text

    feedback_prompt = prompt_manager.get_grammar_feedback_prompt(
        user_message,
        entity_name,
        session_data.designPhase,
    )

    try:
        feedback_response = llm_provider.generate_response(feedback_prompt)
        feedback_duration = get_latest_llm_timing()
    except Exception as exc:  # pylint: disable=broad-except
        logging.error("Error generating writing feedback: %s", exc)
        feedback_response = content_manager.get_bot_response("encouragement.creative_writing")
        feedback_duration = 0.0

    session_data.designAspectHistory.append(session_data.currentDesignAspect)

    aspects = load_design_aspects(entity_type)
    remaining_aspects = [
        aspect for aspect in aspects.keys() if aspect not in session_data.designAspectHistory
    ]
    should_continue_design = (
        len(session_data.designAspectHistory) < 2 and len(remaining_aspects) > 0
    )

    if should_continue_design:
        session_data.currentDesignAspect = remaining_aspects[0]
        feedback_with_transition = (
            f"{feedback_response}\n\nWonderful! Now let's add more details to make {entity_name} even more interesting!"
        )
        design_response = create_design_prompt(session_data)
        design_response.response = feedback_with_transition
        return design_response

    session_data.currentDesignAspect = None

    story_context = " | ".join(session_data.storyParts[-3:]) if session_data.storyParts else ""
    design_summary = (
        "The child has helped design {name} with these details from our design session."
    ).format(name=entity_name)

    continuation_prompt = prompt_manager.get_design_continuation_prompt(
        session_data.topic,
        story_context,
        design_summary,
        user_message,
        entity_name,
    )

    enhanced_prompt, _ = prompt_manager.enhance_with_vocabulary(
        continuation_prompt,
        session_data.topic,
        session_data.askedVocabWords + session_data.contentVocabulary,
    )

    try:
        story_continuation = llm_provider.generate_response(enhanced_prompt)
        story_duration = get_latest_llm_timing()
        session_data.storyParts.append(story_continuation)

        story_vocab = extract_vocabulary_from_content(
            story_continuation,
            session_data.contentVocabulary,
        )
        if story_vocab:
            session_data.contentVocabulary.extend(story_vocab)
            logger.info(
                "📋 VOCABULARY TRACKING: Added %s words from design continuation. Total: %s",
                len(story_vocab),
                len(session_data.contentVocabulary),
            )

        feedback_educational_data = collect_educational_data(
            session_data,
            ChatResponse(response=feedback_response),
            "storywriting",
            provided_description,
            ["grammar_feedback"],
            sub_interaction=1,
        )
        latency_logger.log_educational_interaction(
            "grammar_feedback",
            feedback_response,
            feedback_duration,
            feedback_educational_data,
        )

        story_educational_data = collect_educational_data(
            session_data,
            ChatResponse(response=story_continuation),
            "storywriting",
            provided_description,
            ["story_generation"],
            sub_interaction=2,
        )
        latency_logger.log_educational_interaction(
            "story_generation",
            story_continuation,
            story_duration,
            story_educational_data,
        )

        complete_response = (
            f"{feedback_response}\n\nPerfect! You've helped bring {entity_name} to life! "
            f"Here's how the story continues:\n\n{story_continuation}"
        )

        finalize_current_entity(session_data)
        return ChatResponse(
            response=complete_response,
            sessionData=session_data,
            immediate_logging_performed=True,
        )

    except Exception as exc:  # pylint: disable=broad-except
        logging.error("❌ Error generating enhanced story continuation after design: %s", exc)
        finalize_current_entity(session_data)
        completion_message = (
            f"{feedback_response}\n\nThanks for helping design {entity_name}! "
            "Let's continue our story. What happens next?"
        )
        return ChatResponse(
            response=completion_message,
            sessionData=session_data,
        )


__all__ = [
    "create_design_prompt",
    "finalize_current_entity",
    "get_next_design_aspect",
    "get_next_design_entity",
    "handle_design_phase_interaction",
    "load_design_aspects",
    "resolve_entity_name",
    "trigger_enhanced_design_phase",
    "validate_entity_structure",
]

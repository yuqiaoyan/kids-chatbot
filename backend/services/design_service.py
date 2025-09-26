"""Design-phase helpers for storywriting interactions."""

import json
import logging
import random
from typing import List, Optional, Tuple

from content_manager import content_manager
from llm_provider import llm_provider
from prompt_manager import prompt_manager
from telemetry import collect_educational_data, get_latest_llm_timing, latency_logger
from .vocabulary_service import extract_vocabulary_from_content, log_vocabulary_debug_info
from models.schemas import (
    ChatResponse,
    DesignPrompt,
    EnhancedStoryResponse,
    SessionData,
    StoryEntities,
    StoryMetadata,
    StorywritingPhase,
    StructuredStoryResponse,
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


def determine_entity_type_from_descriptor(metadata: StoryMetadata) -> Optional[str]:
    """Determine whether entity_descriptor refers to a character or location."""
    if not metadata.entity_descriptor:
        return None

    descriptor = metadata.entity_descriptor.strip().lower()

    if metadata.character_description:
        char_desc = metadata.character_description.strip().lower()
        if descriptor == char_desc or descriptor in char_desc or char_desc in descriptor:
            logger.info(
                "🔍 ENTITY TYPE: '%s' matches character_description '%s'",
                metadata.entity_descriptor,
                metadata.character_description,
            )
            return "character"

    if metadata.location_description:
        loc_desc = metadata.location_description.strip().lower()
        if descriptor == loc_desc or descriptor in loc_desc or loc_desc in descriptor:
            logger.info(
                "🔍 ENTITY TYPE: '%s' matches location_description '%s'",
                metadata.entity_descriptor,
                metadata.location_description,
            )
            return "location"

    logger.warning(
        "🔍 ENTITY TYPE: Could not determine type for '%s' - char: '%s', loc: '%s'",
        metadata.entity_descriptor,
        metadata.character_description,
        metadata.location_description,
    )
    return None


def select_design_focus(
    character_name: Optional[str],
    location_name: Optional[str],
    design_options: Optional[List[str]] = None,
    metadata: Optional[StoryMetadata] = None,
) -> Optional[str]:
    """Select character design focus (location design currently disabled)."""
    available_options: List[str] = []

    if character_name:
        available_options.append("character")

    if not available_options and design_options:
        available_options = [option for option in design_options if option == "character"]
        logger.info(
            "🔧 UNNAMED ENTITY: Using design_options %s -> available (character only): %s",
            design_options,
            available_options,
        )

        if metadata and metadata.needs_naming and metadata.entity_descriptor:
            entity_type = determine_entity_type_from_descriptor(metadata)
            if entity_type == "character" and entity_type in available_options:
                logger.info(
                    "🎯 NAMING BUG FIX: entity_descriptor '%s' -> entity_type '%s'",
                    metadata.entity_descriptor,
                    entity_type,
                )
                return entity_type
            if entity_type == "location":
                logger.info(
                    "🚫 LOCATION DESIGN DISABLED: Skipping location entity '%s'",
                    metadata.entity_descriptor,
                )
                return None

    if not available_options:
        return None

    choice = available_options[0]
    logger.info("✅ CHARACTER DESIGN SELECTED: '%s' from %s", choice, available_options)
    return choice


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


def create_design_prompt(session_data: SessionData) -> ChatResponse:
    """Generate design prompt based on current design phase and aspect."""
    if not session_data.designPhase or not session_data.storyMetadata:
        logging.error("create_design_prompt called without active design phase")
        return ChatResponse(
            response=content_manager.get_bot_response("story_mode.continue_story"),
            sessionData=session_data,
        )

    if session_data.designPhase == "character":
        subject_name = session_data.storyMetadata.character_name or "our character"
        subject_description = session_data.storyMetadata.character_description or ""
        if session_data.currentDesignAspect == "naming" and session_data.storyMetadata.entity_descriptor:
            subject_descriptor = session_data.storyMetadata.entity_descriptor
        else:
            subject_descriptor = subject_description
    else:
        subject_name = session_data.storyMetadata.location_name or "this place"
        subject_description = session_data.storyMetadata.location_description or ""
        if session_data.currentDesignAspect == "naming" and session_data.storyMetadata.entity_descriptor:
            subject_descriptor = session_data.storyMetadata.entity_descriptor
        else:
            subject_descriptor = subject_description

    if not session_data.currentDesignAspect:
        session_data.currentDesignAspect = get_next_design_aspect(
            session_data.designPhase,
            session_data.designAspectHistory,
        )

    aspects = load_design_aspects(session_data.designPhase)
    aspect_data = aspects.get(session_data.currentDesignAspect, {})

    if not aspect_data:
        if session_data.currentDesignAspect == "naming":
            aspect_data = {
                "prompt_template": "What should we call {descriptor}?",
                "placeholder": "What would you like to name them?",
                "suggestions": ["Alex", "Maya", "Sam", "Riley", "Jordan", "Casey", "Taylor", "Morgan"],
            }
        else:
            aspect_data = {
                "prompt_template": f"Help us describe {subject_name}!",
                "placeholder": "Write 1-2 sentences describing them",
                "suggestions": [
                    "wonderful",
                    "amazing",
                    "special",
                    "unique",
                    "interesting",
                    "fantastic",
                    "incredible",
                    "magical",
                ],
            }

    if session_data.currentDesignAspect == "naming":
        prompt_text = aspect_data.get("prompt_template", "").format(descriptor=subject_descriptor)
        placeholder_text = aspect_data.get("placeholder", "").format(descriptor=subject_descriptor)
    else:
        prompt_text = aspect_data.get("prompt_template", "").format(name=subject_name)
        placeholder_text = aspect_data.get("placeholder", "Write 1-2 sentences")

    design_prompt = DesignPrompt(
        type=session_data.designPhase,
        subject_name=subject_name,
        aspect=session_data.currentDesignAspect,
        prompt_text=prompt_text,
        suggested_words=aspect_data.get("suggestions", []),
        input_placeholder=placeholder_text,
    )

    return ChatResponse(
        response="",
        sessionData=session_data,
        designPrompt=design_prompt,
    )


def should_trigger_design_phase(structured_response: StructuredStoryResponse) -> bool:
    """Determine if design phase should be triggered based on story response."""
    metadata = structured_response.metadata
    return bool(metadata.design_options and len(metadata.design_options) == 0)


def trigger_design_phase(
    session_data: SessionData, structured_response: StructuredStoryResponse
) -> ChatResponse:
    """Initialize design phase based on legacy story metadata."""
    metadata = structured_response.metadata
    session_data.storyMetadata = metadata

    session_data.designPhase = select_design_focus(
        metadata.character_name,
        metadata.location_name,
        metadata.design_options,
        metadata,
    )

    if not session_data.designPhase:
        logging.info("No design options available, skipping design phase")
        return ChatResponse(
            response=structured_response.story,
            sessionData=session_data,
        )

    session_data.currentDesignAspect = None
    session_data.designAspectHistory = []
    session_data.designComplete = False
    session_data.namingComplete = False

    character_has_name = metadata.character_name and metadata.character_name.strip()
    location_has_name = metadata.location_name and metadata.location_name.strip()
    entity_already_named = character_has_name or location_has_name

    if metadata.needs_naming and not session_data.namingComplete and not entity_already_named:
        logging.info("🏷️ Entity needs naming first: %s", metadata.entity_descriptor)
        session_data.currentDesignAspect = "naming"

        design_response = create_design_prompt(session_data)
        design_response.response = structured_response.story
        return design_response

    if entity_already_named:
        logging.info(
            "🏷️ Entity already named (%s), skipping naming phase",
            metadata.character_name or metadata.location_name,
        )
        session_data.designAspectHistory.append("naming")

    logging.info(
        "Triggering design phase for %s: %s",
        session_data.designPhase,
        metadata.character_name or metadata.location_name,
    )

    design_response = create_design_prompt(session_data)
    design_response.response = structured_response.story
    return design_response


def trigger_enhanced_design_phase(
    session_data: SessionData, enhanced_response: EnhancedStoryResponse
) -> ChatResponse:
    """Initialize design phase using the entity-based metadata system."""
    entities = enhanced_response.entities

    if not hasattr(session_data, "designedEntities"):
        session_data.designedEntities = []
    if not hasattr(session_data, "currentEntityType"):
        session_data.currentEntityType = None
    if not hasattr(session_data, "currentEntityDescriptor"):
        session_data.currentEntityDescriptor = None

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
    session_data.designPhase = entity_type

    if is_named:
        session_data.namingComplete = True
        aspects = ["appearance", "personality", "dreams", "skills"]
        session_data.currentDesignAspect = random.choice(aspects)
        logging.info(
            "🎯 ENHANCED DESIGN: Named entity '%s' - starting with %s aspect",
            entity_descriptor,
            session_data.currentDesignAspect,
        )
    else:
        session_data.currentDesignAspect = "naming"
        logging.info("🎯 ENHANCED DESIGN: Unnamed entity '%s' - starting with naming", entity_descriptor)

    logging.info("🎯 ENHANCED DESIGN: Starting design for %s '%s'", entity_type, entity_descriptor)

    design_response = create_enhanced_design_prompt(session_data)
    design_response.response = enhanced_response.story
    return design_response


def create_enhanced_design_prompt(session_data: SessionData) -> ChatResponse:
    """Create design prompt using enhanced entity system."""
    entity_type = session_data.currentEntityType
    entity_descriptor = session_data.currentEntityDescriptor
    current_aspect = session_data.currentDesignAspect

    if current_aspect == "naming":
        try:
            naming_template = content_manager.get_design_template(f"{entity_type}.naming")
            prompt_text = f"Can you name {entity_descriptor}?"
            if entity_type == "character":
                suggested_words = ["Alex", "Maya", "Sam", "River", "Sky", "Sage", "Blake", "Quinn"]
            else:
                suggested_words = [
                    "Crystal Palace",
                    "Mystic Falls",
                    "Adventure Park",
                    "Sunset Beach",
                    "Secret Garden",
                    "Magic Library",
                    "Wonder Cave",
                    "Star Station",
                ]

            placeholder = f"Enter a name for {entity_descriptor}"

            design_prompt = DesignPrompt(
                type=entity_type,
                subject_name=entity_descriptor,
                aspect=current_aspect,
                prompt_text=prompt_text,
                suggested_words=suggested_words,
                input_placeholder=placeholder,
            )

            return ChatResponse(
                response="",
                sessionData=session_data,
                designPrompt=design_prompt,
            )

        except Exception as exc:  # pylint: disable=broad-except
            logging.error("❌ Failed to create enhanced naming prompt: %s", exc)
            design_prompt = DesignPrompt(
                type=entity_type,
                subject_name=entity_descriptor,
                aspect="naming",
                prompt_text=f"Can you name {entity_descriptor}?",
                suggested_words=["Alex", "Maya", "Sam", "River"],
                input_placeholder=f"Enter a name for {entity_descriptor}",
            )

            return ChatResponse(
                response="",
                sessionData=session_data,
                designPrompt=design_prompt,
            )

    try:
        entity_name = "the entity"
        if getattr(session_data, "currentEntityDescriptor", None):
            descriptor = session_data.currentEntityDescriptor
            if descriptor and len(descriptor.split()) == 1 and descriptor[0].isupper():
                entity_name = descriptor
                logging.info(
                    "🎯 ENHANCED ENTITY NAME: Using currentEntityDescriptor '%s' (detected as proper name)",
                    entity_name,
                )
            elif session_data.storyMetadata:
                if entity_type == "character" and session_data.storyMetadata.character_name:
                    entity_name = session_data.storyMetadata.character_name
                    logging.info(
                        "🎯 ENHANCED ENTITY NAME: Using storyMetadata character_name '%s'",
                        entity_name,
                    )
                elif entity_type == "location" and session_data.storyMetadata.location_name:
                    entity_name = session_data.storyMetadata.location_name
                    logging.info(
                        "🎯 ENHANCED ENTITY NAME: Using storyMetadata location_name '%s'",
                        entity_name,
                    )
                else:
                    entity_name = descriptor
                    logging.info(
                        "🎯 ENHANCED ENTITY NAME: Using currentEntityDescriptor '%s' as fallback",
                        entity_name,
                    )
            else:
                entity_name = descriptor
                logging.info(
                    "🎯 ENHANCED ENTITY NAME: Using currentEntityDescriptor '%s' (no storyMetadata)",
                    entity_name,
                )
        elif session_data.storyMetadata:
            if entity_type == "character" and session_data.storyMetadata.character_name:
                entity_name = session_data.storyMetadata.character_name
            elif entity_type == "location" and session_data.storyMetadata.location_name:
                entity_name = session_data.storyMetadata.location_name

        design_templates = content_manager.content.get("design_templates", {})
        entity_templates = design_templates.get(entity_type, {})
        aspect_data = entity_templates.get(current_aspect, {})

        prompt_template = aspect_data.get("prompt_template", "Tell us more about {name}!")
        placeholder = aspect_data.get("placeholder", "Write 1-2 sentences")
        suggestions = aspect_data.get(
            "suggestions",
            ["brave", "curious", "kind", "imaginative", "talented", "friendly", "helpful", "creative"],
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

        return ChatResponse(
            response="",
            sessionData=session_data,
            designPrompt=design_prompt,
        )

    except Exception as exc:  # pylint: disable=broad-except
        logging.error("❌ Failed to create enhanced description prompt: %s", exc)
        return create_design_prompt(session_data)


async def handle_design_phase_interaction(
    user_message: str, session_data: SessionData
) -> ChatResponse:
    """Handle user input during design phase."""
    # if not session_data.designPhase or not session_data.currentDesignAspect or not session_data.conversationPhase == StorywritingPhase.DESIGNING:
    #     logging.error("handle_design_phase_interaction called without active design phase")
    #     return ChatResponse(
    #         response=content_manager.get_bot_response("story_mode.continue_story"),
    #         sessionData=session_data,
    #     )

    if session_data.currentDesignAspect == "naming":
        provided_name = user_message.strip()

        if getattr(session_data, "currentEntityType", None):
            if not hasattr(session_data, "designedEntities"):
                session_data.designedEntities = []
            session_data.designedEntities.append(session_data.currentEntityDescriptor)

            if not session_data.storyMetadata:
                session_data.storyMetadata = StoryMetadata()

            if session_data.currentEntityType == "character":
                session_data.storyMetadata.character_name = provided_name
            else:
                session_data.storyMetadata.location_name = provided_name

            logging.info(
                "🏷️ ENHANCED NAMING: Named '%s' as '%s'",
                session_data.currentEntityDescriptor,
                provided_name,
            )
        elif session_data.storyMetadata:
            if session_data.designPhase == "character":
                session_data.storyMetadata.character_name = provided_name
            else:
                session_data.storyMetadata.location_name = provided_name
            logging.info("🏷️ LEGACY NAMING: Updated %s name to '%s'", session_data.designPhase, provided_name)
        else:
            session_data.storyMetadata = StoryMetadata()
            if session_data.designPhase == "character":
                session_data.storyMetadata.character_name = provided_name
            else:
                session_data.storyMetadata.location_name = provided_name
            logging.info("🏷️ INIT NAMING: Created storyMetadata and set %s name to '%s'", session_data.designPhase, provided_name)

        session_data.namingComplete = True
        session_data.designAspectHistory.append("naming")

        feedback_response = content_manager.get_bot_response(
            "design_phase.naming_feedback",
            provided_name=provided_name,
            design_phase=session_data.designPhase,
        )

        if getattr(session_data, "currentEntityType", None):
            entity_type = session_data.currentEntityType
            try:
                design_templates = content_manager.content.get("design_templates", {})
                entity_templates = design_templates.get(entity_type, {})
                available_aspects = [
                    aspect
                    for aspect in entity_templates.keys()
                    if aspect != "naming" and aspect not in session_data.designAspectHistory
                ]

                if available_aspects and len(session_data.designAspectHistory) < 2:
                    selected_aspect = random.choice(available_aspects)
                    session_data.currentDesignAspect = selected_aspect
                    logging.info(
                        "🎯 ENHANCED DESIGN: Continuing to %s aspect for %s",
                        selected_aspect,
                        entity_type,
                    )

                    transition_message = (
                        f"Now let's bring {provided_name} to life with more details!"
                    )
                    feedback_with_transition = f"{feedback_response}\n\n{transition_message}"

                    design_response = create_enhanced_design_prompt(session_data)
                    design_response.response = feedback_with_transition
                    return design_response

            except Exception as exc:  # pylint: disable=broad-except
                logging.error("❌ Failed to continue enhanced design phase: %s", exc)

            session_data.designComplete = True
            session_data.designPhase = None
            session_data.currentDesignAspect = None
            session_data.currentEntityType = None
            session_data.currentEntityDescriptor = None

            completion_message = (
                f"{feedback_response}\n\nGreat! Now that we've designed {provided_name}, "
                "let's continue with our story!"
            )

            return ChatResponse(
                response=completion_message,
                sessionData=session_data,
            )

        aspects = load_design_aspects(session_data.designPhase)
        remaining_aspects = [
            aspect for aspect in aspects if aspect not in session_data.designAspectHistory and aspect != "naming"
        ]

        if remaining_aspects and len(session_data.designAspectHistory) < 2:
            session_data.currentDesignAspect = remaining_aspects[0]

            transition_message = content_manager.get_bot_response(
                "design_phase.naming_transition",
                provided_name=provided_name,
            )
            feedback_with_transition = f"{feedback_response}\n\n{transition_message}"
            design_response = create_design_prompt(session_data)
            design_response.response = feedback_with_transition
            return design_response

        session_data.designComplete = True
        session_data.designPhase = None
        session_data.currentDesignAspect = None

        design_completion = content_manager.get_bot_response(
            "design_phase.design_completion_simple",
            feedback_response=feedback_response,
            subject_name=provided_name,
        )

        return ChatResponse(
            response=design_completion,
            sessionData=session_data,
        )

    if getattr(session_data, "currentEntityType", None) and session_data.currentDesignAspect != "naming":
        entity_type = session_data.currentEntityType
        provided_description = user_message.strip()

        entity_name = "the entity"
        if session_data.storyMetadata:
            if entity_type == "character" and session_data.storyMetadata.character_name:
                entity_name = session_data.storyMetadata.character_name
            elif entity_type == "location" and session_data.storyMetadata.location_name:
                entity_name = session_data.storyMetadata.location_name

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

        aspects = load_design_aspects(session_data.designPhase)
        remaining_aspects = [aspect for aspect in aspects if aspect not in session_data.designAspectHistory]

        should_continue_design = len(session_data.designAspectHistory) < 2 and len(remaining_aspects) > 0

        if should_continue_design:
            session_data.currentDesignAspect = remaining_aspects[0]

            feedback_with_transition = (
                f"{feedback_response}\n\nWonderful! Now let's add more details to make {entity_name} even more interesting!"
            )

            design_response = create_design_prompt(session_data)
            design_response.response = feedback_with_transition

            return design_response

        session_data.designComplete = True
        session_data.currentDesignAspect = None

        story_context = " | ".join(session_data.storyParts[-3:]) if session_data.storyParts else ""
        design_summary = "The child has helped design {name} with these details from our design session.".format(
            name=entity_name
        )

        continuation_prompt = prompt_manager.get_design_continuation_prompt(
            session_data.topic,
            story_context,
            design_summary,
            user_message,
            entity_name,
        )

        enhanced_prompt, selected_vocab = prompt_manager.enhance_with_vocabulary(
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

            logging.info(
                "✅ ENHANCED STORY CONTINUATION: Generated continuation after designing %s",
                entity_name,
            )

            return ChatResponse(
                response=complete_response,
                sessionData=session_data,
                immediate_logging_performed=True,
            )

        except Exception as exc:  # pylint: disable=broad-except
            logging.error("❌ Error generating enhanced story continuation after design: %s", exc)
            completion_message = (
                f"{feedback_response}\n\nThanks for helping design {entity_name}! "
                "Let's continue our story. What happens next?"
            )

            return ChatResponse(
                response=completion_message,
                sessionData=session_data,
            )

    if session_data.storyMetadata:
        subject_name = (
            session_data.storyMetadata.character_name
            if session_data.designPhase == "character"
            else session_data.storyMetadata.location_name
        )
    else:
        subject_name = "the entity"
        logging.warning("Regular design aspect handling called but storyMetadata is None")

    feedback_prompt = prompt_manager.get_grammar_feedback_prompt(
        user_message,
        subject_name,
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

    aspects = load_design_aspects(session_data.designPhase)
    remaining_aspects = [aspect for aspect in aspects if aspect not in session_data.designAspectHistory]

    should_continue_design = len(session_data.designAspectHistory) < 2 and len(remaining_aspects) > 0

    if should_continue_design:
        session_data.currentDesignAspect = remaining_aspects[0]

        feedback_with_transition = (
            f"{feedback_response}\n\nWonderful! Now let's add more details to make {subject_name} even more interesting!"
        )

        design_response = create_design_prompt(session_data)
        design_response.response = feedback_with_transition

        return design_response

    session_data.designComplete = True
    session_data.currentDesignAspect = None

    story_context = " | ".join(session_data.storyParts[-3:]) if session_data.storyParts else ""
    design_summary = (
        "The child has helped design {subject_name} with these details from our design session."
    ).format(subject_name=subject_name)

    continuation_prompt = prompt_manager.get_design_continuation_prompt(
        session_data.topic,
        story_context,
        design_summary,
        user_message,
        subject_name,
    )

    enhanced_prompt, selected_vocab = prompt_manager.enhance_with_vocabulary(
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

        logging.info(
            "🐛 DEBUG: About to log grammar feedback. Response length: %s",
            len(feedback_response),
        )
        feedback_educational_data = collect_educational_data(
            session_data,
            ChatResponse(response=feedback_response),
            "storywriting",
            user_message,
            ["grammar_feedback"],
            sub_interaction=1,
        )
        logging.info("🐛 DEBUG: Calling log_educational_interaction for grammar_feedback")
        latency_logger.log_educational_interaction(
            "grammar_feedback",
            feedback_response,
            feedback_duration,
            feedback_educational_data,
        )
        logging.info("🐛 DEBUG: Grammar feedback logging completed")

        logging.info(
            "🐛 DEBUG: About to log story generation. Response length: %s",
            len(story_continuation),
        )
        story_educational_data = collect_educational_data(
            session_data,
            ChatResponse(response=story_continuation),
            "storywriting",
            user_message,
            ["story_generation"],
            sub_interaction=2,
        )
        logging.info("🐛 DEBUG: Calling log_educational_interaction for story_generation")
        latency_logger.log_educational_interaction(
            "story_generation",
            story_continuation,
            story_duration,
            story_educational_data,
        )
        logging.info("🐛 DEBUG: Story generation logging completed")

        complete_response = (
            f"{feedback_response}\n\nPerfect! You've helped bring {subject_name} to life! "
            f"Here's how the story continues:\n\n{story_continuation}"
        )

        return ChatResponse(
            response=complete_response,
            sessionData=session_data,
            immediate_logging_performed=True,
        )

    except Exception as exc:  # pylint: disable=broad-except
        logging.error("Error generating story continuation after design: %s", exc)
        return ChatResponse(
            response=(
                f"{feedback_response}\n\nThanks for helping design {subject_name}! "
                "Let's continue our story. What happens next?"
            ),
            sessionData=session_data,
        )


__all__ = [
    "create_design_prompt",
    "create_enhanced_design_prompt",
    "determine_entity_type_from_descriptor",
    "get_next_design_aspect",
    "get_next_design_entity",
    "handle_design_phase_interaction",
    "load_design_aspects",
    "select_design_focus",
    "should_trigger_design_phase",
    "trigger_design_phase",
    "trigger_enhanced_design_phase",
    "validate_entity_structure",
]

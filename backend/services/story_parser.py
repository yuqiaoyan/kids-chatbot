"""Story parsing helpers for structured and enhanced responses."""

import json
import logging
from typing import Tuple

from models.schemas import (
    EntityLists,
    EnhancedStoryResponse,
    StoryEntities,
    StoryMetadata,
    StructuredStoryResponse,
)

logger = logging.getLogger(__name__)


def parse_structured_story_response(llm_response: str) -> StructuredStoryResponse:
    """Parse JSON response with fallback to plain text if parsing fails."""
    try:
        data = json.loads(llm_response.strip())

        if not isinstance(data, dict) or "story" not in data:
            raise ValueError("Invalid JSON structure - missing 'story' field")

        metadata_dict = data.get("metadata", {})
        design_options = metadata_dict.get("design_options", [])
        if not design_options:
            logger.warning("⚠️ VALIDATION: design_options is empty or missing in LLM response")
            if metadata_dict.get("character_name") or metadata_dict.get("character_description"):
                design_options = ["character"]
                logger.info("🔧 AUTO-FIX: Inferred design_options as ['character'] from metadata")

        metadata = StoryMetadata(
            character_name=metadata_dict.get("character_name"),
            character_description=metadata_dict.get("character_description"),
            location_name=metadata_dict.get("location_name"),
            location_description=metadata_dict.get("location_description"),
            design_options=design_options,
            needs_naming=metadata_dict.get("needs_naming", False),
            entity_descriptor=metadata_dict.get("entity_descriptor"),
        )

        return StructuredStoryResponse(story=data["story"], metadata=metadata)

    except (json.JSONDecodeError, ValueError, KeyError) as exc:
        logger.warning("Failed to parse structured story response: %s", exc)
        logger.warning("Raw response: %s...", llm_response[:200])

        return StructuredStoryResponse(
            story=llm_response,
            metadata=StoryMetadata(design_options=[]),
        )


def parse_enhanced_story_response(llm_response: str) -> EnhancedStoryResponse:
    """Parse JSON response with new entity-based metadata structure."""
    try:
        data = json.loads(llm_response.strip())

        if not isinstance(data, dict) or "story" not in data:
            raise ValueError("Invalid JSON structure - missing 'story' field")

        entities_dict = data.get("entities", {})
        if not isinstance(entities_dict, dict):
            logger.warning("⚠️ VALIDATION: entities field is not a dictionary, using empty entities")
            entities_dict = {}

        characters_dict = entities_dict.get("characters", {})
        if not isinstance(characters_dict, dict):
            logger.warning("⚠️ VALIDATION: entities.characters is not a dictionary, using empty lists")
            characters_dict = {}

        named_chars = characters_dict.get("named", [])
        unnamed_chars = characters_dict.get("unnamed", [])
        if not isinstance(named_chars, list):
            logger.warning("⚠️ VALIDATION: characters.named is not a list, converting to empty list")
            named_chars = []
        if not isinstance(unnamed_chars, list):
            logger.warning("⚠️ VALIDATION: characters.unnamed is not a list, converting to empty list")
            unnamed_chars = []

        locations_dict = entities_dict.get("locations", {})
        if not isinstance(locations_dict, dict):
            logger.warning("⚠️ VALIDATION: entities.locations is not a dictionary, using empty lists")
            locations_dict = {}

        named_locs = locations_dict.get("named", [])
        unnamed_locs = locations_dict.get("unnamed", [])
        if not isinstance(named_locs, list):
            logger.warning("⚠️ VALIDATION: locations.named is not a list, converting to empty list")
            named_locs = []
        if not isinstance(unnamed_locs, list):
            logger.warning("⚠️ VALIDATION: locations.unnamed is not a list, converting to empty list")
            unnamed_locs = []

        vocab_words = data.get("vocabulary_words", [])
        if not isinstance(vocab_words, list):
            logger.warning("⚠️ VALIDATION: vocabulary_words is not a list, converting to empty list")
            vocab_words = []

        entities = StoryEntities(
            characters=EntityLists(named=named_chars, unnamed=unnamed_chars),
            locations=EntityLists(named=named_locs, unnamed=unnamed_locs),
        )

        total_entities = len(named_chars) + len(unnamed_chars) + len(named_locs) + len(unnamed_locs)
        logger.info(
            "✅ ENTITY PARSE: Found %s entities - chars(%s named, %s unnamed), locs(%s named, %s unnamed)",
            total_entities,
            len(named_chars),
            len(unnamed_chars),
            len(named_locs),
            len(unnamed_locs),
        )

        return EnhancedStoryResponse(
            story=data["story"],
            entities=entities,
            vocabulary_words=vocab_words,
        )

    except (json.JSONDecodeError, ValueError, KeyError) as exc:
        logger.warning("Failed to parse enhanced story response: %s", exc)
        logger.warning("Raw response: %s...", llm_response[:200])

        try:
            legacy_response = parse_structured_story_response(llm_response)
            logger.info("🔧 FALLBACK: Successfully used legacy parser")

            entities = StoryEntities()
            if legacy_response.metadata.character_name:
                entities.characters.named = [legacy_response.metadata.character_name]
            elif legacy_response.metadata.character_description:
                entities.characters.unnamed = [legacy_response.metadata.character_description]

            if legacy_response.metadata.location_name:
                entities.locations.named = [legacy_response.metadata.location_name]
            elif legacy_response.metadata.location_description:
                entities.locations.unnamed = [legacy_response.metadata.location_description]

            return EnhancedStoryResponse(
                story=legacy_response.story,
                entities=entities,
                vocabulary_words=[],
            )

        except Exception as legacy_error:  # pylint: disable=broad-except
            logger.error("❌ FALLBACK FAILED: Legacy parser also failed: %s", legacy_error)
            return EnhancedStoryResponse(
                story=llm_response,
                entities=StoryEntities(),
                vocabulary_words=[],
            )


__all__ = [
    "parse_enhanced_story_response",
    "parse_structured_story_response",
]

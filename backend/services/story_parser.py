"""Story parsing helpers for structured and enhanced responses."""

import json
import logging
from models.schemas import EntityLists, EnhancedStoryResponse, StoryEntities

logger = logging.getLogger(__name__)


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
        logger.error("Failed to parse enhanced story response: %s", exc)
        logger.error("Raw response preview: %s", llm_response[:200])
        raise ValueError("Enhanced story response parsing failed") from exc


__all__ = ["parse_enhanced_story_response"]

"""Theme configuration helpers for topic extraction and suggestions."""

import json
import logging
import os
from typing import Dict


def load_theme_config() -> Dict[str, Dict[str, list]]:
    """Load theme configuration from centralized JSON file."""
    config_path = os.path.join(
        os.path.dirname(__file__),
        "..",
        "..",
        "frontend",
        "config",
        "theme-config.json",
    )
    config_path = os.path.abspath(config_path)
    try:
        with open(config_path, "r", encoding="utf-8") as file:
            config = json.load(file)
            logging.info("Theme configuration loaded successfully from JSON file")
            return config
    except Exception as exc:  # pylint: disable=broad-except
        logging.error("Failed to load theme configuration: %s", exc)
        return {
            "topicKeywords": {
                "space": ["space", "planet", "star", "rocket", "astronaut"],
                "animals": ["animal", "dog", "cat"],
                "fantasy": ["fantasy", "magic", "magical", "dragon", "creature"],
                "sports": ["sport", "soccer", "football"],
                "ocean": ["ocean", "sea", "fish"],
            },
            "themeMapping": {
                "fantasy": "theme-fantasy",
                "sports": "theme-sports",
                "animals": "theme-animals",
                "ocean": "theme-ocean",
                "space": "theme-space",
            },
            "defaultTheme": "theme-space",
        }


THEME_CONFIG = load_theme_config()


def extract_topic_from_message(message: str, config: Dict[str, Dict[str, list]] = THEME_CONFIG) -> str:
    """Extract topic from user message using configured keywords."""
    message_lower = message.lower()

    for topic, keywords in config.get("topicKeywords", {}).items():
        if any(keyword in message_lower for keyword in keywords):
            return topic

    words = message.split()
    return words[0] if words else "adventure"


def get_theme_suggestion(topic: str, config: Dict[str, Dict[str, list]] = THEME_CONFIG) -> str:
    """Map topic to theme suggestion for frontend using configured mapping."""
    return config.get("themeMapping", {}).get(topic.lower(), config.get("defaultTheme", "theme-space"))


__all__ = [
    "THEME_CONFIG",
    "extract_topic_from_message",
    "get_theme_suggestion",
    "load_theme_config",
]

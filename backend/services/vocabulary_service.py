"""Vocabulary selection, extraction, and logging helpers."""

import logging
import re
from typing import Dict, List, Optional

from prompt_manager import prompt_manager
from vocabulary_manager import vocabulary_manager

logger = logging.getLogger(__name__)


def select_advanced_general_words(count: int = 20, exclude: Optional[List[str]] = None) -> List[str]:
    """Select tier 2+3 words from the expanded general vocabulary pool."""
    exclude = exclude or []

    general_vocabulary = vocabulary_manager.general_vocabulary
    advanced_words = [word for word in general_vocabulary if word["difficulty"] in [2, 3]]

    preferred_words = [word for word in advanced_words if word["word"] not in exclude]
    if len(preferred_words) >= count:
        selected_words = preferred_words[:count]
    else:
        additional_needed = count - len(preferred_words)
        used_words = [word for word in advanced_words if word["word"] in exclude]
        selected_words = preferred_words + used_words[:additional_needed]

    return [word["word"] for word in selected_words[:count]]


def get_all_topic_vocabulary(topic: str) -> List[str]:
    """Return the list of all topic-specific vocabulary words."""
    topic_vocabulary = vocabulary_manager.get_vocabulary_for_topic(topic)

    topic_lower = topic.lower()
    if topic_lower in vocabulary_manager.topic_vocabularies:
        topic_words = vocabulary_manager.topic_vocabularies[topic_lower]
        return [word["word"] for word in topic_words]

    return []


def extract_vocabulary_from_content(content: str, content_vocabulary: Optional[List[str]] = None) -> List[str]:
    """Extract vocabulary words from generated content, prioritizing bolded tokens."""
    content_vocabulary = content_vocabulary or []
    bolded_words = re.findall(r"\*\*(.*?)\*\*", content)

    extracted_words: List[str] = []
    for word in bolded_words:
        clean_word = word.strip()
        if clean_word and len(clean_word) > 1:
            extracted_words.append(clean_word)

    context_words: List[str] = []
    for word in extracted_words:
        for vocab_word in content_vocabulary:
            if vocab_word.lower() == word or vocab_word.lower() in word.lower():
                context_words.append(vocab_word)
                break
        else:
            context_words.append(word)

    seen = set()
    unique_words: List[str] = []
    for word in context_words:
        lowered = word.lower()
        if lowered not in seen:
            seen.add(lowered)
            unique_words.append(word)

    logger.info("Extracted vocabulary from content: %s", unique_words)
    return unique_words


def log_vocabulary_debug_info(
    topic: str,
    used_words: List[str],
    content: str,
    context: str,
    session_total: int,
) -> None:
    """Log vocabulary debug information to server logs."""
    vocab_pools = prompt_manager.generate_massive_vocabulary_pool(topic, used_words)
    bolded_words = re.findall(r"\*\*(.*?)\*\*", content)

    debug_info: Dict[str, object] = {
        "general_pool": vocab_pools.get("general_pool", []),
        "topic_pool": vocab_pools.get("topic_pool", []),
        "excluded_words": vocab_pools.get("excluded_words", []),
        "total_available": vocab_pools.get("total_examples", 0),
        "llm_selected_words": bolded_words,
        "context": context,
        "content_preview": content[:100] + ("..." if len(content) > 100 else ""),
        "session_total": session_total,
    }

    logging.info("🔍 DEBUG: Created %s vocab debug info: %s", context.lower(), debug_info)


def select_best_vocabulary_word(available_words: List[str]) -> Optional[str]:
    """Select the best vocabulary word while filtering multi-word proper nouns."""
    if not available_words:
        logger.info("select_best_vocabulary_word: No available words")
        return None

    logger.info("select_best_vocabulary_word: Available words: %s", available_words)

    single_word_candidates = [word for word in available_words if word and len(word.split()) == 1]
    for word in available_words:
        if word not in single_word_candidates:
            logger.info("select_best_vocabulary_word: Skipping multi-word: '%s'", word)

    if single_word_candidates:
        lowercase_candidates = [word for word in single_word_candidates if word.islower()]
        if lowercase_candidates:
            selected = lowercase_candidates[0]
            logger.info("select_best_vocabulary_word: Selected lowercase word: '%s'", selected)
            return selected

        selected = single_word_candidates[0]
        logger.info("select_best_vocabulary_word: Selected (fallback): '%s'", selected)
        return selected

    selected = available_words[0]
    logger.warning("select_best_vocabulary_word: Using last resort word: '%s'", selected)
    return selected


__all__ = [
    "extract_vocabulary_from_content",
    "get_all_topic_vocabulary",
    "log_vocabulary_debug_info",
    "select_advanced_general_words",
    "select_best_vocabulary_word",
]

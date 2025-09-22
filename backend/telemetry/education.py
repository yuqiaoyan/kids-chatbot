"""Educational logging helpers and prompt telemetry utilities."""
import re
from typing import Dict, List, Optional

from content_manager import content_manager
from models.schemas import ChatResponse, SessionData, VocabQuestion


def classify_interaction_module(
    session_data: SessionData,
    response: ChatResponse,
    mode: str,
    llm_call_types: List[str],
) -> str:
    """Classify interaction by educational purpose using LLM call types and session state."""

    if getattr(response, "vocabQuestion", None):
        return "vocabulary"

    if "grammar_feedback" in llm_call_types:
        return "llm_feedback"

    if getattr(session_data, "designPhase", None) and not getattr(session_data, "designComplete", False):
        return "character_design"

    if mode == "funfacts":
        return "fun_fact"

    return "storywriting_narrative"


def extract_prompt_template_previews(prompt_files: List[str], module: str) -> Dict[str, Dict[str, str]]:
    """Return preview data for prompt templates based on the current module."""

    previews: Dict[str, Dict[str, str]] = {}

    try:
        if module == "vocabulary" and "shared_prompts" in prompt_files:
            shared_content = content_manager.content.get("shared_prompts", {})
            vocab_system = shared_content.get("vocabulary_system", {})
            question_gen = vocab_system.get("question_generation", {})
            if "prompt_template" in question_gen:
                template = question_gen["prompt_template"]
                previews["vocabulary_question_generation"] = {
                    "definition": "question_generation",
                    "file": "shared_prompts",
                    "template_preview": template[:250] + ("..." if len(template) > 250 else ""),
                }

        elif module == "character_design" and "character_design_prompts" in prompt_files:
            char_content = content_manager.content.get("character_design_prompts", {})
            naming_prompts = char_content.get("naming_prompts", {})
            character_naming = naming_prompts.get("character", {})
            if "prompt_template" in character_naming:
                template = character_naming["prompt_template"]
                previews["character_naming"] = {
                    "definition": "naming_prompts.character",
                    "file": "character_design_prompts",
                    "template_preview": template[:250] + ("..." if len(template) > 250 else ""),
                }

        elif module == "fun_fact" and "funfacts_prompts" in prompt_files:
            facts_content = content_manager.content.get("funfacts_prompts", {})
            content_gen = facts_content.get("content_generation", {})
            fact_templates = content_gen.get("fact_templates", {})
            for template_key in ["space", "general", "animals"]:
                template_data = fact_templates.get(template_key)
                if template_data and "template" in template_data:
                    template = template_data["template"]
                    previews[f"fact_template_{template_key}"] = {
                        "definition": f"fact_templates.{template_key}",
                        "file": "funfacts_prompts",
                        "template_preview": template[:250] + ("..." if len(template) > 250 else ""),
                    }
                    break

        elif module in {"storywriting_narrative", "llm_feedback"} and "storywriting_prompts" in prompt_files:
            story_content = content_manager.content.get("storywriting_prompts", {})
            story_gen = story_content.get("story_generation", {})
            story_opening = story_gen.get("story_opening", {})
            for template_type in ["named_entities", "unnamed_entities"]:
                template_data = story_opening.get(template_type)
                if template_data and "prompt_template" in template_data:
                    template = template_data["prompt_template"]
                    previews[f"story_opening_{template_type}"] = {
                        "definition": f"story_generation.story_opening.{template_type}",
                        "file": "storywriting_prompts",
                        "template_preview": template[:250] + ("..." if len(template) > 250 else ""),
                    }
                    break

            story_ending = story_gen.get("story_ending", {})
            if "prompt_template" in story_ending:
                template = story_ending["prompt_template"]
                previews["story_ending"] = {
                    "definition": "story_generation.story_ending",
                    "file": "storywriting_prompts",
                    "template_preview": template[:250] + ("..." if len(template) > 250 else ""),
                }

        if not previews:
            for file_key in prompt_files:
                content_data = content_manager.content.get(file_key)
                if isinstance(content_data, dict):
                    available_keys = list(content_data.keys())
                    previews[f"{file_key}_available_sections"] = {
                        "definition": f"Available sections in {file_key}",
                        "file": file_key,
                        "template_preview": f"Available sections: {', '.join(available_keys[:10])}",
                    }

        return previews

    except Exception as exc:  # pylint: disable=broad-except
        return {"error": f"Template extraction failed: {exc}"}


def get_relevant_prompt_versions(module: str, mode: str) -> Dict[str, Dict[str, str]]:
    """Return relevant prompt version metadata and previews for logging."""

    try:
        all_versions = content_manager.get_prompt_versions()
        relevant_versions: Dict[str, Dict[str, str]] = {}

        if module == "vocabulary" and "shared_prompts" in all_versions:
            relevant_versions["shared_prompts"] = all_versions["shared_prompts"]
        elif module == "character_design":
            if "character_design_prompts" in all_versions:
                relevant_versions["character_design_prompts"] = all_versions["character_design_prompts"]
            if "shared_prompts" in all_versions:
                relevant_versions["shared_prompts"] = all_versions["shared_prompts"]
        elif module == "fun_fact":
            if "funfacts_prompts" in all_versions:
                relevant_versions["funfacts_prompts"] = all_versions["funfacts_prompts"]
            if "shared_prompts" in all_versions:
                relevant_versions["shared_prompts"] = all_versions["shared_prompts"]
        else:
            if "storywriting_prompts" in all_versions:
                relevant_versions["storywriting_prompts"] = all_versions["storywriting_prompts"]
            if "shared_prompts" in all_versions:
                relevant_versions["shared_prompts"] = all_versions["shared_prompts"]

        prompt_previews = extract_prompt_template_previews(list(relevant_versions.keys()), module)
        return {"versions": relevant_versions, "previews": prompt_previews}

    except Exception as exc:  # pylint: disable=broad-except
        return {
            "versions": {"error": f"Version tracking failed: {exc}"},
            "previews": {},
        }


def extract_vocabulary_interaction_data(
    vocab_question: VocabQuestion,
    user_input: str,
) -> Dict[str, Optional[str]]:
    """Derive user interaction metadata for vocabulary questions."""

    try:
        word_match = re.search(r"\*\*([^*]+)\*\*", vocab_question.question)
        target_word = word_match.group(1) if word_match else "unknown"

        reference_sentence = ""
        for line in vocab_question.question.split("\n"):
            stripped = line.strip()
            if stripped.startswith("\"") and stripped.endswith("\""):
                reference_sentence = stripped.strip('"')
                break

        user_selected_answer = None
        user_selected_index = None
        if user_input:
            answer_match = re.search(r"[aAbBcCdD]", user_input.strip())
            if answer_match:
                user_selected_answer = answer_match.group(0).upper()
                user_selected_index = ord(user_selected_answer) - ord("A")

        is_correct = (
            user_selected_index == vocab_question.correctIndex
            if user_selected_index is not None
            else None
        )

        correct_answer_text: Optional[str] = None
        if 0 <= vocab_question.correctIndex < len(vocab_question.options):
            correct_answer_text = vocab_question.options[vocab_question.correctIndex]

        return {
            "target_word": target_word,
            "question_text": vocab_question.question,
            "reference_sentence": reference_sentence,
            "options": vocab_question.options,
            "correct_answer_index": vocab_question.correctIndex,
            "correct_answer_text": correct_answer_text,
            "user_selected_answer": user_selected_answer,
            "user_selected_index": user_selected_index,
            "is_correct": is_correct,
            "raw_user_input": user_input,
        }

    except Exception as exc:  # pylint: disable=broad-except
        return {
            "error": f"Vocabulary data extraction failed: {exc}",
            "raw_vocab_question": vocab_question.dict() if hasattr(vocab_question, "dict") else str(vocab_question),
            "raw_user_input": user_input,
        }


def extract_educational_context(session_data: SessionData, mode: str) -> Dict[str, Optional[str]]:
    """Compile educational context data for telemetry logging."""

    try:
        story_quality = {
            "character_growth_score": session_data.characterGrowthScore,
            "completeness_score": session_data.completenessScore,
            "conflict_type": session_data.conflictType,
            "conflict_scale": session_data.conflictScale,
            "has_assessment": session_data.narrativeAssessment is not None,
        }

        return {
            "current_topic": session_data.topic,
            "interaction_mode": mode,
            "story_quality": story_quality,
            "age_band": "2nd-3rd_grade",
            "facts_shown": session_data.factsShown if mode == "funfacts" else None,
        }

    except Exception as exc:  # pylint: disable=broad-except
        return {
            "error": f"Educational context extraction failed: {exc}",
            "current_topic": getattr(session_data, "topic", None),
            "interaction_mode": mode,
            "age_band": "2nd-3rd_grade",
        }


def collect_educational_data(
    session_data: SessionData,
    response: ChatResponse,
    mode: str,
    user_input: str,
    llm_call_types: List[str],
    assessment_result: Optional[dict] = None,
    sub_interaction: int = 1,
) -> Dict[str, object]:
    """Aggregate educational metadata for downstream logging."""

    module = classify_interaction_module(session_data, response, mode, llm_call_types)
    prompt_versions = get_relevant_prompt_versions(module, mode)
    educational_context = extract_educational_context(session_data, mode)

    educational_data = {
        "session_id": session_data.session_id,
        "turn_id": session_data.turn_id,
        "sub_interaction": sub_interaction,
        "story_id": session_data.current_story_id,
        "funfact_id": session_data.current_funfact_id,
        "module": module,
        "user_input": user_input,
        "ai_output": getattr(response, "response", ""),
        "story_assessment": assessment_result,
        "educational_context": educational_context,
        "prompt_versions": prompt_versions,
    }

    if module == "vocabulary" and getattr(response, "vocabQuestion", None):
        vocab_data = extract_vocabulary_interaction_data(response.vocabQuestion, user_input)
        educational_data["vocabulary_interaction"] = vocab_data

    return educational_data


__all__ = [
    "classify_interaction_module",
    "collect_educational_data",
    "extract_educational_context",
    "extract_prompt_template_previews",
    "extract_vocabulary_interaction_data",
    "get_relevant_prompt_versions",
]

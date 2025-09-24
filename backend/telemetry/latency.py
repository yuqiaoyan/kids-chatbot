"""Latency logging utilities and shared telemetry state."""

import glob
import logging
import os
from datetime import datetime

from latency_logger import LatencyLogger
from llm_provider import llm_call_timings
from models.schemas import ChatResponse, SessionData
# from story_tracker import StoryLatencyTracker

MAX_LOG_SIZE = 5_000_000  # 5MB rotation threshold


def rotate_logs_if_needed() -> None:
    """Rotate latency logs when they exceed the size threshold."""
    log_files = [
        "logs/latency.jsonl",
        "logs/story_latency.jsonl",
    ]

    for log_file in log_files:
        if os.path.exists(log_file) and os.path.getsize(log_file) > MAX_LOG_SIZE:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            archive_name = f"{log_file}.{timestamp}.archive"
            print(f"📁 Rotating log file: {log_file} -> {archive_name}")
            os.rename(log_file, archive_name)


def cleanup_old_archives(keep_count: int = 5) -> None:
    """Keep only the most recent archive files for latency logs."""
    for log_type in ["latency.jsonl", "story_latency.jsonl"]:
        pattern = f"logs/{log_type}.*.archive"
        archives = glob.glob(pattern)

        if len(archives) > keep_count:
            archives.sort(key=os.path.getmtime)
            old_archives = archives[:-keep_count]

            for old_file in old_archives:
                os.remove(old_file)
                print(f"🗑️ Cleaned up old archive: {old_file}")


def setup_latency_logging() -> None:
    """Configure latency logging and ensure rotation strategy is in place."""
    os.makedirs("logs", exist_ok=True)
    rotate_logs_if_needed()
    cleanup_old_archives(keep_count=5)

    latency_logger = logging.getLogger("latency")
    latency_logger.setLevel(logging.INFO)
    latency_logger.handlers.clear()

    handler = logging.FileHandler("logs/latency.jsonl", mode="a")
    handler.setFormatter(logging.Formatter("%(message)s"))
    latency_logger.addHandler(handler)


def get_latest_llm_timing() -> float:
    """Return the duration of the most recent LLM call."""
    if llm_call_timings:
        return llm_call_timings[-1].get("duration", 0.0)
    return 0.0


def determine_story_exchange_type(session_data: SessionData, result: ChatResponse) -> str:
    """Classify story interaction type for latency tracking."""
    if getattr(result, "vocabQuestion", None):
        return "vocab_question"

    if getattr(session_data, "designPhase", None):
        return "design_phase"

    if getattr(session_data, "isComplete", False):
        return "story_completion"

    if not session_data.topic or session_data.currentStep == 0:
        return "topic_selection"

    return "story_continuation"


latency_logger = LatencyLogger()
# story_tracker = StoryLatencyTracker()

__all__ = [
    "cleanup_old_archives",
    "determine_story_exchange_type",
    "get_latest_llm_timing",
    "latency_logger",
    "rotate_logs_if_needed",
    "setup_latency_logging",
    # "story_tracker",
]

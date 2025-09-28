"""Session lifecycle and content ID management helpers."""

import logging
import uuid
from datetime import datetime, timedelta

from models.schemas import SessionData


def manage_session_lifecycle(session_data: SessionData, current_time: datetime) -> None:
    """Manage session lifecycle with 30-minute timeout handling."""
    if not session_data.session_id:
        session_data.session_id = str(uuid.uuid4())
        session_data.session_start = current_time
        session_data.turn_id = 1
        session_data.last_activity = current_time
        logging.info(
            "🆔 NEW SESSION: Created session %s... at %s",
            session_data.session_id[:8],
            current_time,
        )
        session_data.last_activity = current_time
        return

    if session_data.last_activity:
        inactive_duration = current_time - session_data.last_activity
        if inactive_duration > timedelta(minutes=30):
            old_session_id = session_data.session_id[:8] if session_data.session_id else "unknown"
            session_data.session_id = str(uuid.uuid4())
            session_data.session_start = current_time
            session_data.turn_id = 1
            session_data.current_story_id = None
            session_data.current_funfact_id = None
            session_data.story_history = []
            session_data.funfact_history = []
            logging.info(
                "⏰ SESSION TIMEOUT: Started new session %s... (previous: %s...)",
                session_data.session_id[:8],
                old_session_id,
            )
        else:
            session_data.turn_id += 1
    else:
        session_data.session_start = current_time
        session_data.turn_id += 1

    session_data.last_activity = current_time


def manage_content_ids(session_data: SessionData, mode: str) -> None:
    """Automatically manage story and fun fact identifiers based on mode."""
    if mode == "storywriting":
        if not session_data.current_story_id:
            session_data.current_story_id = str(uuid.uuid4())
            session_data.story_history.append(session_data.current_story_id)
            logging.info(
                "📖 NEW STORY: Created story ID %s... in session %s...",
                session_data.current_story_id[:8],
                session_data.session_id[:8] if session_data.session_id else "unknown",
            )
        if session_data.current_funfact_id:
            session_data.current_funfact_id = None
            logging.info("🔄 MODE SWITCH: Cleared funfact ID (switched to story)")
        return

    if mode == "funfacts":
        if not session_data.current_funfact_id:
            session_data.current_funfact_id = str(uuid.uuid4())
            session_data.funfact_history.append(session_data.current_funfact_id)
            logging.info(
                "🔍 NEW FUNFACTS: Created funfact ID %s... in session %s...",
                session_data.current_funfact_id[:8],
                session_data.session_id[:8] if session_data.session_id else "unknown",
            )
        if session_data.current_story_id:
            session_data.current_story_id = None
            logging.info("🔄 MODE SWITCH: Cleared story ID (switched to funfacts)")


__all__ = [
    "manage_content_ids",
    "manage_session_lifecycle",
]

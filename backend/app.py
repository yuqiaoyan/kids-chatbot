from datetime import datetime
import logging
import os
import time

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from content_manager import content_manager
from models.schemas import ChatRequest, ChatResponse, SessionData
from services import (
    handle_funfacts,
    handle_storywriting,
    manage_content_ids,
    manage_session_lifecycle,
)
from telemetry import (
    collect_educational_data,
    determine_story_exchange_type,
    latency_logger,
    setup_latency_logging,
    # story_tracker,
)

logger = logging.getLogger(__name__)

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def startup_event() -> None:
    """Initialize latency logging when the service starts."""
    setup_latency_logging()
    logger.info("Latency logging initialized with 5MB rotation")


@app.post("/chat", response_model=ChatResponse)
async def chat_endpoint(chat_request: ChatRequest) -> ChatResponse:
    """Primary chat endpoint handling storywriting and fun-fact flows."""
    request_start = time.perf_counter()

    try:
        user_message = chat_request.message
        mode = chat_request.mode
        session_data = chat_request.sessionData or SessionData()

        # Manage session lifecycle and ensure IDs are set for logging
        current_time = datetime.now()
        manage_session_lifecycle(session_data, current_time)
        manage_content_ids(session_data, mode)

        logger.info(
            "Processing %s message: %s [Session: %s..., Turn: %s]",
            mode,
            user_message,
            session_data.session_id[:8] if session_data.session_id else "none",
            session_data.turn_id,
        )

        if mode == "storywriting":
            story_mode = chat_request.storyMode or "auto"

            # if not session_data.topic or session_data.currentStep == 0:
            #     story_tracker.start_story(topic=session_data.topic or "unknown", mode=mode)

            result = await handle_storywriting(user_message, session_data, story_mode)

            request_latency = (time.perf_counter() - request_start) * 1000
            exchange_type = determine_story_exchange_type(session_data, result)
            # story_tracker.log_exchange(
            #     exchange_type=exchange_type,
            #     latency=request_latency,
            #     user_input=user_message,
            #     response_type=getattr(result, "response_type", "story_response"),
            # )

            # if result.sessionData and result.sessionData.isComplete:
            #     story_summary = story_tracker.complete_story(
            #         topic=result.sessionData.topic or "unknown",
            #         mode=mode,
            #     )
            #     if hasattr(result, "latency_summary"):
            #         result.latency_summary = story_summary

            if not getattr(result, "immediate_logging_performed", False):
                llm_call_types = latency_logger.get_current_llm_call_types()
                educational_data = collect_educational_data(
                    session_data=session_data,
                    response=result,
                    mode=mode,
                    user_input=user_message,
                    llm_call_types=llm_call_types,
                )
                result.educational_data = educational_data

            return result

        if mode == "funfacts":
            result = await handle_funfacts(user_message, session_data)

            if not getattr(result, "immediate_logging_performed", False):
                llm_call_types = latency_logger.get_current_llm_call_types()
                educational_data = collect_educational_data(
                    session_data=session_data,
                    response=result,
                    mode=mode,
                    user_input=user_message,
                    llm_call_types=llm_call_types,
                )
                result.educational_data = educational_data

            return result

        return ChatResponse(response=content_manager.get_bot_response("errors.mode_error"))

    except Exception as exc:  # pylint: disable=broad-except
        logger.error("Error processing chat request: %s", exc)
        return ChatResponse(response=content_manager.get_bot_response("errors.processing_error"))


@app.get("/health")
async def health_check() -> dict:
    """Lightweight health endpoint for uptime checks."""
    return {"status": "healthy", "message": "English Learning Chatbot API is running!"}


frontend_path = os.path.join(os.path.dirname(__file__), "..", "frontend")
app.mount("/", StaticFiles(directory=frontend_path, html=True), name="static")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)

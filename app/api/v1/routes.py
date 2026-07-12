"""API v1 routes — Zepto voice agent endpoints."""

import asyncio
import logging

from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from app.agent.orchestrator import (
    confirm_session,
    get_session,
    run_execute,
    run_parse,
    say_to_session,
)
from app.models.schemas import (
    ConfirmRequest,
    ParseRequest,
    ParseResponse,
    SayRequest,
    SessionStatus,
)

logger = logging.getLogger(__name__)

router = APIRouter()


def _respond(session, since: int = 0) -> SessionStatus:
    """Build a SessionStatus response, returning only messages after *since*."""
    status = session.to_status()
    total = status.total_messages
    status.messages = session.messages[since:] if since < total else []
    return status


# ═══════════════════════════════════════════════════════════════════════
#  Agent endpoints
# ═══════════════════════════════════════════════════════════════════════


@router.post("/agent/parse", response_model=ParseResponse)
async def parse_command(req: ParseRequest):
    """Parse a voice/text command into a structured shopping order.

    Returns a ``session_id`` the frontend should use for all subsequent calls.
    The session already contains the first agent message (NL response).
    """
    logger.info("POST /agent/parse — text (len=%d): %.150s", len(req.text), req.text)
    session = await run_parse(req.text)
    if session.status == "failed":
        logger.error("Parse failed: %s", session.error)
        raise HTTPException(status_code=422, detail=session.error)
    logger.info("→ session=%s, status=%s, items=%d",
                session.session_id, session.status,
                len(session.parsed_order.items) if session.parsed_order else 0)
    return ParseResponse(session_id=session.session_id, parsed=session.parsed_order)


@router.post("/agent/execute/{session_id}", response_model=SessionStatus)
async def execute_order(session_id: str, since: int = Query(default=0, ge=0)):
    """Start executing a parsed order — searches Zepto, adds to cart,
    then pauses for user confirmation."""
    logger.info("POST /agent/execute/%s", session_id)
    session = get_session(session_id)
    print(f"session : ")
    if not session:
        logger.warning("Session %s not found", session_id)
        raise HTTPException(status_code=404, detail="Session not found")
    if session.status != "parsed":
        logger.warning("Session %s is in state '%s', expected 'parsed'", session_id, session.status)
        raise HTTPException(
            status_code=400,
            detail=f"Session is in state '{session.status}', expected 'parsed'",
        )

    logger.info("Spawning background execution task for session %s", session_id)
    asyncio.create_task(run_execute(session_id))
    await asyncio.sleep(0.5)

    current = get_session(session_id)
    status = current.status if current else "unknown"
    logger.info("→ session=%s → status=%s", session_id, status)
    return _respond(current, since)


@router.get("/agent/status/{session_id}", response_model=SessionStatus)
async def get_status(session_id: str, since: int = Query(default=0, ge=0)):
    """Poll the current status — returns new messages since the given index."""
    session = get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    logger.debug("GET /agent/status/%s → %s (since=%d)", session_id, session.status, since)
    return _respond(session, since)


@router.post("/agent/confirm/{session_id}", response_model=SessionStatus)
async def confirm_order(session_id: str, body: ConfirmRequest):
    """Submit confirmation (or cancellation) for an awaiting-confirmation order."""
    logger.info("POST /agent/confirm/%s — confirmed=%s", session_id, body.confirmed)
    session = confirm_session(session_id, body.confirmed)
    if not session:
        logger.warning("Cannot confirm session %s — not found or not awaiting confirmation", session_id)
        raise HTTPException(
            status_code=400,
            detail="Session not found or not awaiting confirmation",
        )
    logger.info("→ session=%s → status=%s", session_id, session.status)
    return _respond(session, body.since)


@router.post("/agent/say", response_model=SessionStatus)
async def say_to_agent(req: SayRequest):
    """Send a spoken / typed utterance to the agent.

    If the agent is waiting for confirmation, yes/no is auto-detected.
    Only new messages (since the given index) are returned.
    """
    logger.info("POST /agent/say — session=%s , text (len=%d): %.150s",
                req.session_id or "(new)", len(req.text), req.text)

    if not req.session_id:
        # Start fresh: parse the text as a new command
        logger.info("No session_id — creating new session from text")
        session = await run_parse(req.text)
        if session.status == "failed":
            logger.error("New session parse failed: %s", session.error)
            raise HTTPException(status_code=422, detail=session.error)
        logger.info("→ new session=%s, status=%s", session.session_id, session.status)
        return _respond(session, req.since)

    session = await say_to_session(req.session_id, req.text)
    if not session:
        logger.warning("Session %s not found for /say", req.session_id)
        raise HTTPException(status_code=404, detail="Session not found")

    logger.info("→ session=%s → status=%s, msg_count=%d",
                session.session_id, session.status, len(session.messages))
    return _respond(session, req.since)


@router.get("/agent/sessions", response_model=list[SessionStatus])
async def list_sessions():
    """Return all active sessions (for debugging)."""
    from app.agent.orchestrator import _sessions

    count = len(_sessions)
    logger.debug("GET /agent/sessions — %d active sessions", count)
    return [s.to_status() for s in _sessions.values()]


# ═══════════════════════════════════════════════════════════════════════
#  Text-to-Speech
# ═══════════════════════════════════════════════════════════════════════


@router.get("/agent/tts")
async def text_to_speech(text: str = Query(..., min_length=1, max_length=500)):
    """Convert text to speech audio (AIFF) using pyttsx3 (JARVIS voice).

    Returns the cached audio file.  First call generates it; subsequent
    calls serve the cached version instantly.
    """
    logger.debug("TTS requested: %.80s…", text)
    from app.voice.tts import text_to_audio as generate_audio

    audio_path = generate_audio(text)
    if not audio_path:
        raise HTTPException(status_code=500, detail="TTS generation failed")

    file_path = f"app/static/audio/{Path(audio_path).name}"
    logger.debug("TTS serving: %s", file_path)
    return FileResponse(file_path, media_type="audio/wav", filename=Path(file_path).name)


# ═══════════════════════════════════════════════════════════════════════
#  Static frontend
# ═══════════════════════════════════════════════════════════════════════


@router.get("/app")
async def serve_app():
    """Serve the voice-agent frontend."""
    logger.debug("GET /app — serving frontend")
    return FileResponse("app/static/index.html")

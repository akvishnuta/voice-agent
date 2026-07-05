"""API v1 routes — Zepto voice agent endpoints."""

import asyncio
import logging

from fastapi import APIRouter, HTTPException
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


# ═══════════════════════════════════════════════════════════════════════
#  Agent endpoints
# ═══════════════════════════════════════════════════════════════════════


@router.post("/agent/parse", response_model=ParseResponse)
async def parse_command(req: ParseRequest):
    """Parse a voice/text command into a structured shopping order.

    Returns a ``session_id`` the frontend should use for all subsequent calls.
    The session already contains the first agent message (NL response).
    """
    session = await run_parse(req.text)
    if session.status == "failed":
        raise HTTPException(status_code=422, detail=session.error)
    return ParseResponse(session_id=session.session_id, parsed=session.parsed_order)


@router.post("/agent/execute/{session_id}", response_model=SessionStatus)
async def execute_order(session_id: str):
    """Start executing a parsed order — searches Zepto, adds to cart,
    then pauses for user confirmation."""
    session = get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.status != "parsed":
        raise HTTPException(
            status_code=400,
            detail=f"Session is in state '{session.status}', expected 'parsed'",
        )

    asyncio.create_task(run_execute(session_id))
    await asyncio.sleep(0.5)
    return session.to_status()


@router.get("/agent/status/{session_id}", response_model=SessionStatus)
async def get_status(session_id: str):
    """Poll the current status — returns full state including message history."""
    session = get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return session.to_status()


@router.post("/agent/confirm/{session_id}", response_model=SessionStatus)
async def confirm_order(session_id: str, body: ConfirmRequest):
    """Submit confirmation (or cancellation) for an awaiting-confirmation order."""
    session = confirm_session(session_id, body.confirmed)
    if not session:
        raise HTTPException(
            status_code=400,
            detail="Session not found or not awaiting confirmation",
        )
    return session.to_status()


@router.post("/agent/say", response_model=SessionStatus)
async def say_to_agent(req: SayRequest):
    """Send a spoken / typed utterance to the agent.

    If the agent is waiting for confirmation, yes/no is auto-detected.
    The full message history is returned so the frontend can render the chat.
    """
    if not req.session_id:
        # Start fresh: parse the text as a new command
        session = await run_parse(req.text)
        if session.status == "failed":
            raise HTTPException(status_code=422, detail=session.error)
        return session.to_status()

    session = await say_to_session(req.session_id, req.text)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return session.to_status()


@router.get("/agent/sessions", response_model=list[SessionStatus])
async def list_sessions():
    """Return all active sessions (for debugging)."""
    from app.agent.orchestrator import _sessions

    return [s.to_status() for s in _sessions.values()]


# ═══════════════════════════════════════════════════════════════════════
#  Static frontend
# ═══════════════════════════════════════════════════════════════════════


@router.get("/app")
async def serve_app():
    """Serve the voice-agent frontend."""
    return FileResponse("app/static/index.html")

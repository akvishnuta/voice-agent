"""Order orchestration — ties LLM parsing → browser automation → confirmation."""

import asyncio
import logging
import uuid
from dataclasses import dataclass, field

from app.agent.parser import parse_command, generate_nl_response
from app.browser.zepto_client import ZeptoClient, ZeptoSessionError
from app.config import settings
from app.voice.tts import text_to_audio
from app.models.schemas import (
    ChatMessage,
    ParsedOrder,
    ProductResult,
    SessionStatus,
)

logger = logging.getLogger(__name__)


@dataclass
class Session:
    """In-memory state for one order conversation."""

    session_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    status: str = "init"
    step: str = ""
    error: str | None = None
    parsed_order: ParsedOrder | None = None
    cart_items: list[ProductResult] = field(default_factory=list)
    cart_total: float | None = None
    confirmation_needed: str | None = None
    confirmation_data: dict | None = None
    otp_requested: bool = False
    messages: list[ChatMessage] = field(default_factory=list)
    user_command: str = ""

    _confirmed_event: asyncio.Event = field(default_factory=asyncio.Event)
    _confirmed_value: bool = False
    _otp_event: asyncio.Event = field(default_factory=asyncio.Event)
    _otp_value: str = ""

    def to_status(self) -> SessionStatus:
        return SessionStatus(
            session_id=self.session_id,
            status=self.status,
            step=self.step,
            error=self.error,
            parsed_order=self.parsed_order,
            cart_items=self.cart_items,
            cart_total=self.cart_total,
            confirmation_needed=self.confirmation_needed,
            confirmation_data=self.confirmation_data,
            otp_requested=self.otp_requested,
            messages=self.messages,
            total_messages=len(self.messages),
        )

    def add_message(self, role: str, text: str, msg_type: str = "message",
                    audio_url: str | None = None) -> None:
        self.messages.append(
            ChatMessage(role=role, text=text, type=msg_type, audio_url=audio_url)
        )

    def add_agent_message(self, text: str, msg_type: str = "message") -> None:
        logger.info("[%s] Agent says [%s]: %.100s", self.session_id, msg_type, text)
        audio_url = text_to_audio(text)
        self.messages.append(
            ChatMessage(role="agent", text=text, type=msg_type, audio_url=audio_url)
        )


_sessions: dict[str, Session] = {}


def get_session(session_id: str) -> Session | None:
    session = _sessions.get(session_id)
    if session:
        logger.debug("[%s] Session fetched — status=%s", session_id, session.status)
    else:
        logger.warning("[%s] Session not found", session_id)
    return session


def create_session() -> Session:
    session = Session()
    _sessions[session.session_id] = session
    logger.info("=" * 60)
    logger.info("SESSION CREATED: %s", session.session_id)
    logger.info("=" * 60)
    return session


# ── Parse ─────────────────────────────────────────────────────────────

async def run_parse(text: str) -> Session:
    """Parse voice command and return a session with the first agent message."""
    session = create_session()
    session.user_command = text
    session.add_message("user", text)
    logger.info("[%s] PARSE: user text (len=%d): %.150s", session.session_id, len(text), text)

    try:
        logger.info("[%s] Calling LLM parser…", session.session_id)
        parsed = parse_command(text)
        session.parsed_order = parsed
        session.status = "parsed"
        session.step = "Command parsed — awaiting confirmation to proceed"
        logger.info("[%s] Parsed %d items: %s | pref=%s | max_price=%s",
                    session.session_id, len(parsed.items),
                    [i.name for i in parsed.items],
                    parsed.preference, parsed.max_price)

        nl = generate_nl_response("parse_ready", {
            "user_command": text,
            "items": [i.model_dump() for i in parsed.items],
            "item_count": len(parsed.items),
            "max_price": parsed.max_price,
            "preference": parsed.preference,
        })
        session.add_agent_message(nl, "confirmation")
        logger.info("[%s] Ready for user confirmation", session.session_id)

    except Exception as exc:
        session.status = "failed"
        session.error = f"Could not parse command: {exc}"
        session.add_agent_message(
            generate_nl_response("error", {"error": str(exc)}), "error"
        )
        logger.exception("[%s] Parse FAILED", session.session_id)
    return session


# ── Execute ───────────────────────────────────────────────────────────

async def run_execute(session_id: str) -> None:
    """Execute a previously-parsed order — runs as a background coroutine."""
    session = get_session(session_id)
    if not session or not session.parsed_order:
        logger.warning("[%s] Cannot execute — session not found or not parsed", session_id)
        return

    order: ParsedOrder = session.parsed_order
    logger.info("[%s] EXECUTE starting — %d items, dry_run=%s",
                session_id, len(order.items), settings.dry_run)

    try:
        session.status = "searching"
        session.confirmation_needed = None

        async with ZeptoClient() as zepto:
            # ── Login (interactive OTP) ───────────────────────────
            session.step = "Logging into Zepto…"
            logger.info("[%s] Step: launching browser and logging into Zepto", session_id)
            session.add_agent_message("Opening Zepto in your browser Sir…", "progress")
            logged_in = await zepto.login()

            if not logged_in:
                # OTP was sent — ask the user through the chat
                session.otp_requested = True
                logger.info("[%s] OTP sent — asking user for code via chat", session_id)
                session.add_agent_message(
                    "I have sent an OTP to your phone Sir. Please type or say the 4 to 6 digit code here.",
                    "otp"
                )
                # Wait for user to provide the OTP
                await session._otp_event.wait()
                session._otp_event.clear()
                session.otp_requested = False
                otp = session._otp_value
                logger.info("[%s] User provided OTP: %s", session_id, otp)
                session.add_agent_message("Entering the OTP in browser now Sir…", "progress")
                await zepto.submit_otp(otp)
                logger.info("[%s] OTP verified — logged into Zepto", session_id)
                session.add_agent_message("Logged into Zepto successfully Sir! ✅", "done")
            else:
                logger.info("[%s] Already logged in via saved cookies", session_id)
                session.add_agent_message("Already logged into Zepto Sir! ✅", "done")

            # ── Search & add each item ─────────────────────────────
            for idx, item in enumerate(order.items, 1):
                session.step = f'Searching for "{item.name}"…'
                logger.info("[%s] Item %d/%d: searching '%s'",
                            session_id, idx, len(order.items), item.name)
                session.add_agent_message(
                    generate_nl_response("progress_searching",
                                         {"item": item.name}), "progress"
                )
                results = await zepto.search(item.name)

                if not results:
                    msg = f'Item "{item.name}" not found on Zepto'
                    logger.warning("[%s] %s — aborting", session_id, msg)
                    session.add_agent_message(
                        generate_nl_response("progress_not_found",
                                             {"item": item.name}), "progress"
                    )
                    session.status = "failed"
                    session.error = msg
                    return

                selected = _apply_preference(results, order.preference)
                logger.info("[%s] Selected '%s' at ₹%.2f",
                            session_id, selected.name, selected.price)
                session.add_agent_message(
                    generate_nl_response("progress_found_selected", {
                        "item": selected.name,
                        "price": selected.price,
                    }), "progress"
                )
                await zepto.add_to_cart(selected)
                session.cart_items.append(selected)
                logger.info("[%s] Cart now has %d items", session_id, len(session.cart_items))

            # ── Cart summary ───────────────────────────────────────
            logger.info("[%s] Fetching cart summary from browser", session_id)
            session.step = "Fetching cart summary…"
            cart = await zepto.get_cart()
            session.cart_total = cart.get("total") or sum(
                p.price for p in session.cart_items
            )
            logger.info("[%s] Cart total: ₹%.2f (from browser: %s)",
                        session_id, session.cart_total, cart.get("total"))

            # Budget check
            if order.max_price and session.cart_total and session.cart_total > order.max_price:
                logger.warning("[%s] Budget exceeded: ₹%.2f > ₹%.2f",
                               session_id, session.cart_total, order.max_price)
                session.status = "failed"
                session.error = (
                    f"Cart total ₹{session.cart_total:.2f} exceeds "
                    f"your budget of ₹{order.max_price:.2f}"
                )
                session.add_agent_message(
                    generate_nl_response("budget_exceeded", {
                        "total": session.cart_total,
                        "max_price": order.max_price,
                        "items": [],
                    }), "error"
                )
                return

            session.confirmation_needed = "place_order"
            session.confirmation_data = {
                "items": [p.model_dump() for p in session.cart_items],
                "total": session.cart_total,
            }
            session.status = "awaiting_confirmation"
            logger.info("[%s] AWAITING CONFIRMATION — cart total ₹%.2f",
                        session_id, session.cart_total)
            session.add_agent_message(
                generate_nl_response("cart_ready", {
                    "items": [p.model_dump() for p in session.cart_items],
                    "cart_total": session.cart_total,
                    "max_price": order.max_price,
                }), "confirmation"
            )

            # ── Wait for user confirmation ─────────────────────────
            logger.info("[%s] Waiting for user confirmation…", session_id)
            await session._confirmed_event.wait()
            session._confirmed_event.clear()
            logger.info("[%s] User confirmation received: %s",
                        session_id, session._confirmed_value)

            if not session._confirmed_value:
                session.status = "cancelled"
                session.add_agent_message(
                    generate_nl_response("cancelled", {}), "done"
                )
                logger.info("[%s] Order cancelled by user", session_id)
                return

            # ── Checkout (or dry-run) ──────────────────────────────
            if settings.dry_run:
                logger.info("[%s] DRY RUN — skipping checkout", session_id)
                session.status = "completed"
                session.confirmation_needed = None
                session.add_agent_message(
                    generate_nl_response("dry_run_complete", {
                        "items": [p.model_dump() for p in session.cart_items],
                        "cart_total": session.cart_total,
                    }), "done"
                )
            else:
                logger.info("[%s] CHECKOUT — placing order", session_id)
                session.status = "checking_out"
                session.step = "Placing order at Zepto…"
                session.add_agent_message("Placing your order now Sir…", "progress")
                await zepto.checkout()

                session.status = "completed"
                session.confirmation_needed = None
                session.add_agent_message(
                    generate_nl_response("order_placed", {}), "done"
                )
                logger.info("[%s] ORDER PLACED SUCCESSFULLY 🎉", session_id)

    except ZeptoSessionError:
        session.status = "failed"
        session.error = "A Zepto error occurred — check the browser window"
        session.add_agent_message(
            generate_nl_response("error", {
                "error": "Zepto browser error — please check the window"
            }), "error"
        )
        logger.exception("[%s] ZEPTO ERROR", session_id)
    except Exception:
        session.status = "failed"
        session.error = "An unexpected error occurred. Check logs for details."
        session.step = ""
        session.add_agent_message(
            generate_nl_response("error", {"error": "Unexpected error"}), "error"
        )
        logger.exception("[%s] EXECUTION FAILED", session_id)


# ── Confirmation / conversational input ───────────────────────────────

def confirm_session(session_id: str, confirmed: bool) -> Session | None:
    """Submit confirmation for an awaiting-confirmation session."""
    session = get_session(session_id)
    if not session or session.status != "awaiting_confirmation":
        logger.warning("[%s] confirm skipped — status=%s", session_id,
                       getattr(session, "status", "not_found"))
        return None

    session._confirmed_value = confirmed
    session._confirmed_event.set()
    logger.info("[%s] Confirmation: %s", session_id, "YES ✅" if confirmed else "NO ❌")

    if not confirmed:
        session.status = "cancelled"
        session.add_agent_message(
            generate_nl_response("cancelled", {}), "done"
        )
    return session


async def say_to_session(session_id: str, text: str) -> Session | None:
    """Send a user's spoken/typed message to an active session."""
    logger.info("USER Query registered: %.200s", text)
    session = get_session(session_id)
    if not session:
        return None

    logger.info("[%s] USER SAYS: %.200s", session_id, text)
    session.add_message("user", text)

    # If OTP is pending, check if this looks like a code
    if session.otp_requested:
        stripped = text.strip().replace(" ", "")
        if stripped.isdigit() and 4 <= len(stripped) <= 6:
            logger.info("[%s] Detected OTP input: %s", session_id, stripped)
            session._otp_value = stripped
            session._otp_event.set()
            return session
        else:
            logger.info("[%s] OTP requested but input doesn't look like a code (ignoring)", session_id)

    # If parsed and user says "yes" — kick off execution
    if session.status == "parsed":
        affirmative = any(w in text.lower()
                         for w in ("yes", "yeah", "sure", "go ahead",
                                   "proceed", "start", "do it", "okay", "ok"))
        if affirmative:
            logger.info("[%s] User confirmed parsed order — starting execution", session_id)
            session.status = "searching"
            session.add_agent_message("Starting search on Zepto Sir…", "progress")
            asyncio.create_task(run_execute(session_id))
            return session

    # If waiting for confirmation, auto-detect yes/no
    if session.status == "awaiting_confirmation":
        affirmative = any(w in text.lower()
                         for w in ("yes", "yeah", "sure", "go ahead",
                                   "place it", "proceed", "do it", "okay", "ok"))
        negative = any(w in text.lower()
                       for w in ("no", "nah", "cancel", "stop", "don't", "wait", "never mind"))

        if affirmative:
            logger.info("[%s] Auto-detected YES from user utterance", session_id)
            confirm_session(session_id, True)
            session.add_agent_message("Great Sir! Placing your order now…", "progress")
            return session
        elif negative:
            logger.info("[%s] Auto-detected NO from user utterance", session_id)
            confirm_session(session_id, False)
            return session
        else:
            logger.info("[%s] Utterance doesn't match yes/no patterns — ignoring", session_id)

    return session


# ── Helpers ───────────────────────────────────────────────────────────

def _apply_preference(results: list[ProductResult], preference: str) -> ProductResult:
    """Select the best product based on user preference."""
    if preference == "cheapest" and results:
        selected = min(results, key=lambda p: (p.price, p.name))
        logger.info("Cheapest: '%s' (₹%.2f) out of %d results",
                    selected.name, selected.price, len(results))
        return selected
    if preference == "best_rated" and results:
        selected = results[0]
        logger.info("Best rated (first result): '%s' (₹%.2f) out of %d results",
                    selected.name, selected.price, len(results))
        return selected
    selected = results[0]
    logger.info("Default (first): '%s' (₹%.2f)", selected.name, selected.price)
    return selected

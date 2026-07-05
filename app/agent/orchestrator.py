"""Order orchestration — ties LLM parsing → browser automation → confirmation."""

import asyncio
import logging
import uuid
from dataclasses import dataclass, field

from app.agent.parser import parse_command, generate_nl_response
from app.browser.zepto_client import ZeptoClient, ZeptoSessionError
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
    messages: list[ChatMessage] = field(default_factory=list)
    user_command: str = ""

    _confirmed_event: asyncio.Event = field(default_factory=asyncio.Event)
    _confirmed_value: bool = False
    _user_say_event: asyncio.Event = field(default_factory=asyncio.Event)
    _user_say_text: str = ""

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
            messages=self.messages,
        )

    def add_message(self, role: str, text: str, msg_type: str = "message") -> None:
        self.messages.append(ChatMessage(role=role, text=text, type=msg_type))

    def add_agent_message(self, text: str, msg_type: str = "message") -> None:
        self.add_message("agent", text, msg_type)


_sessions: dict[str, Session] = {}


def get_session(session_id: str) -> Session | None:
    return _sessions.get(session_id)


def create_session() -> Session:
    session = Session()
    _sessions[session.session_id] = session
    return session


# ── Parse ─────────────────────────────────────────────────────────────

async def run_parse(text: str) -> Session:
    """Parse voice command and return a session with the first agent message."""
    session = create_session()
    session.user_command = text
    session.add_message("user", text)

    try:
        parsed = parse_command(text)
        session.parsed_order = parsed
        session.status = "parsed"
        session.step = "Command parsed — awaiting confirmation to proceed"

        # Generate natural-language response
        nl = generate_nl_response("parse_ready", {
            "user_command": text,
            "items": [i.model_dump() for i in parsed.items],
            "item_count": len(parsed.items),
            "max_price": parsed.max_price,
            "preference": parsed.preference,
        })
        session.add_agent_message(nl, "confirmation")
    except Exception as exc:
        session.status = "failed"
        session.error = f"Could not parse command: {exc}"
        session.add_agent_message(
            generate_nl_response("error", {"error": str(exc)}), "error"
        )
        logger.exception("Parse failed")
    return session


# ── Execute ───────────────────────────────────────────────────────────

async def run_execute(session_id: str) -> None:
    """Execute a previously-parsed order — runs as a background coroutine."""
    session = get_session(session_id)
    if not session or not session.parsed_order:
        logger.warning("Session %s not found or not parsed", session_id)
        return

    order: ParsedOrder = session.parsed_order
    try:
        session.status = "searching"
        session.confirmation_needed = None

        async with ZeptoClient() as zepto:
            # ── Login ──────────────────────────────────────────────
            session.step = "Logging into Zepto…"
            session.add_agent_message("Opening Zepto in your browser…", "progress")
            await zepto.login()

            # ── Search & add each item ─────────────────────────────
            for item in order.items:
                session.step = f'Searching for "{item.name}"…'
                session.add_agent_message(
                    generate_nl_response("progress_searching",
                                         {"item": item.name}), "progress"
                )
                results = await zepto.search(item.name)

                if not results:
                    msg = f'Item "{item.name}" not found on Zepto'
                    session.add_agent_message(
                        generate_nl_response("progress_not_found",
                                             {"item": item.name}), "progress"
                    )
                    session.status = "failed"
                    session.error = msg
                    logger.warning(msg)
                    return

                selected = _apply_preference(results, order.preference)
                session.add_agent_message(
                    generate_nl_response("progress_found_selected", {
                        "item": selected.name,
                        "price": selected.price,
                    }), "progress"
                )
                await zepto.add_to_cart(selected)
                session.cart_items.append(selected)

            # ── Cart summary ───────────────────────────────────────
            session.step = "Fetching cart summary…"
            cart = await zepto.get_cart()
            session.cart_total = cart.get("total") or sum(
                p.price for p in session.cart_items
            )

            # Budget check
            if order.max_price and session.cart_total and session.cart_total > order.max_price:
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
            session.add_agent_message(
                generate_nl_response("cart_ready", {
                    "items": [p.model_dump() for p in session.cart_items],
                    "cart_total": session.cart_total,
                    "max_price": order.max_price,
                }), "confirmation"
            )

            # ── Wait for user confirmation ─────────────────────────
            await session._confirmed_event.wait()
            session._confirmed_event.clear()

            if not session._confirmed_value:
                session.status = "cancelled"
                session.add_agent_message(
                    generate_nl_response("cancelled", {}), "done"
                )
                return

            # ── Checkout ───────────────────────────────────────────
            session.status = "checking_out"
            session.step = "Placing order at Zepto…"
            session.add_agent_message("Placing your order now…", "progress")
            await zepto.checkout()

            session.status = "completed"
            session.confirmation_needed = None
            session.add_agent_message(
                generate_nl_response("order_placed", {}), "done"
            )

    except ZeptoSessionError:
        session.status = "failed"
        session.error = "A Zepto error occurred — check the browser window"
        session.add_agent_message(
            generate_nl_response("error", {
                "error": "Zepto browser error — please check the window"
            }), "error"
        )
        logger.exception("Zepto error")
    except Exception:
        session.status = "failed"
        session.error = "An unexpected error occurred. Check logs for details."
        session.step = ""
        session.add_agent_message(
            generate_nl_response("error", {"error": "Unexpected error"}), "error"
        )
        logger.exception("Execution failed for session %s", session_id)


# ── Confirmation / conversational input ───────────────────────────────

def confirm_session(session_id: str, confirmed: bool) -> Session | None:
    """Submit confirmation for an awaiting-confirmation session."""
    session = get_session(session_id)
    if not session or session.status != "awaiting_confirmation":
        return None
    session._confirmed_value = confirmed
    session._confirmed_event.set()
    if not confirmed:
        session.status = "cancelled"
        session.add_agent_message(
            generate_nl_response("cancelled", {}), "done"
        )
    return session


async def say_to_session(session_id: str, text: str) -> Session | None:
    """Send a user's spoken/typed message to an active session."""
    session = get_session(session_id)
    if not session:
        return None

    session.add_message("user", text)

    # If waiting for confirmation, auto-detect yes/no
    if session.status == "awaiting_confirmation":
        affirmative = any(w in text.lower()
                         for w in ("yes", "yeah", "sure", "go ahead",
                                   "place it", "proceed", "do it", "okay", "ok"))
        if affirmative:
            confirm_session(session_id, True)
            session.add_agent_message("Great! Placing your order now…", "progress")
            return session
        elif any(w in text.lower()
                 for w in ("no", "nah", "cancel", "stop", "don't", "wait", "never mind")):
            confirm_session(session_id, False)
            return session

    return session


# ── Helpers ───────────────────────────────────────────────────────────

def _apply_preference(results: list[ProductResult], preference: str) -> ProductResult:
    """Select the best product based on user preference."""
    if preference == "cheapest" and results:
        selected = min(results, key=lambda p: (p.price, p.name))
        logger.info("Selected cheapest: '%s' at ₹%.2f", selected.name, selected.price)
        return selected
    if preference == "best_rated" and results:
        selected = results[0]
        logger.info("Selected first result: '%s' at ₹%.2f", selected.name, selected.price)
        return selected
    selected = results[0] if results else results[0]
    logger.info("Selected: '%s' at ₹%.2f", selected.name, selected.price)
    return selected

"""Pydantic request/response schemas."""

from pydantic import BaseModel, Field


class ParsedItem(BaseModel):
    name: str = Field(description="Item name, e.g. 'milk'")
    quantity: int = Field(default=1, ge=1, description="How many units to buy")


class ParsedOrder(BaseModel):
    items: list[ParsedItem] = Field(description="List of items to order")
    preference: str = Field(
        default="cheapest",
        description="Selection preference: cheapest / best_rated / any",
    )
    max_price: float | None = Field(
        default=None, ge=1, description="Maximum total spend (e.g. 600)"
    )
    currency: str = Field(default="INR")


class ParseRequest(BaseModel):
    text: str = Field(min_length=3, description="Raw voice / text command")


class ParseResponse(BaseModel):
    session_id: str
    parsed: ParsedOrder


# ── Execution ────────────────────────────────────────────────────────

class ProductResult(BaseModel):
    name: str
    price: float
    quantity_label: str = ""
    url: str = ""


class ChatMessage(BaseModel):
    role: str = Field(description="user | agent")
    text: str
    type: str = Field(
        default="message",
        description="message | confirmation | progress | error | success | done",
    )


class SessionStatus(BaseModel):
    session_id: str
    status: str = Field(
        description="parsed | searching | adding_to_cart | awaiting_confirmation | checking_out | completed | cancelled | failed"
    )
    step: str = ""
    error: str | None = None
    parsed_order: ParsedOrder | None = None
    cart_items: list[ProductResult] = []
    cart_total: float | None = None
    confirmation_needed: str | None = None
    confirmation_data: dict | None = None
    messages: list[ChatMessage] = []


class ConfirmRequest(BaseModel):
    confirmed: bool


class SayRequest(BaseModel):
    session_id: str = ""
    text: str = Field(min_length=1, description="User's spoken / typed response")

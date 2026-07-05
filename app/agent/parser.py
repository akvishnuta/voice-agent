"""LLM-powered NL parsing and response generation.

Supports DeepSeek (default), OpenAI, and Anthropic providers.
"""

import json
import logging

from app.agent.prompts import PARSE_ORDER_SYSTEM_PROMPT, GENERATE_RESPONSE_PROMPT, TEMPLATES
from app.config import settings
from app.models.schemas import ParsedOrder, ParsedItem

logger = logging.getLogger(__name__)


# ── Helpers ────────────────────────────────────────────────────────────

def _build_user_prompt(text: str) -> str:
    return f"Parse this voice command into a structured order:\n\n{text}"


def _try_parse_json(raw: str) -> dict:
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    return json.loads(text)


def _json_to_parsed_order(data: dict) -> ParsedOrder:
    return ParsedOrder(
        items=[ParsedItem(**it) for it in data.get("items", [])],
        preference=data.get("preference", "cheapest"),
        max_price=data.get("max_price"),
        currency=data.get("currency", "INR"),
    )


def _chat_completion(api_key: str, system: str, user_msg: str,
                     base_url: str | None = None, temperature: float = 0.1) -> str:
    """Generic OpenAI-compatible chat completion returning raw text."""
    from openai import OpenAI

    kwargs = {"api_key": api_key}
    if base_url:
        kwargs["base_url"] = base_url

    client = OpenAI(**kwargs)
    resp = client.chat.completions.create(
        model=settings.llm_model,
        temperature=temperature,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user_msg},
        ],
    )
    return resp.choices[0].message.content.strip()


def _anthropic_completion(system: str, user_msg: str) -> str:
    from anthropic import Anthropic
    client = Anthropic(api_key=settings.anthropic_api_key)
    resp = client.messages.create(
        model=settings.llm_model,
        max_tokens=1024,
        system=system,
        messages=[{"role": "user", "content": user_msg}],
    )
    return resp.content[0].text.strip()


def _provider_call(system: str, user_msg: str, temperature: float = 0.1) -> str:
    provider = settings.llm_provider.lower()
    if provider == "deepseek":
        return _chat_completion(settings.deepseek_api_key, system, user_msg,
                                base_url=settings.deepseek_base_url, temperature=temperature)
    elif provider == "openai":
        return _chat_completion(settings.openai_api_key, system, user_msg,
                                temperature=temperature)
    elif provider == "anthropic":
        return _anthropic_completion(system, user_msg)
    raise ValueError(f"Unsupported LLM provider: '{provider}'")


# ── Public API ─────────────────────────────────────────────────────────

def parse_command(text: str) -> ParsedOrder:
    """Parse a natural-language voice command into a structured order."""
    if not text.strip():
        raise ValueError("Empty command text")
    raw = _provider_call(PARSE_ORDER_SYSTEM_PROMPT, _build_user_prompt(text))
    return _json_to_parsed_order(_try_parse_json(raw))


def generate_nl_response(stage: str, context: dict) -> str:
    """Generate a natural-language spoken response for the current stage.

    Falls back to template-based responses if the LLM call fails.
    """
    text = ""
    user_cmd = context.get("user_command", "")
    items_text = _items_summary(context.get("items", []))
    cart_total = str(context.get("cart_total", 0))
    extra = ""

    if stage == "error":
        extra = f"- Error: {context.get('error', 'unknown')}"
    elif stage == "budget_exceeded":
        extra = f"- The user's max budget is ₹{context.get('max_price', 0)}"

    try:
        prompt = GENERATE_RESPONSE_PROMPT.format(
            user_command=user_cmd[:200],
            stage=stage,
            items_text=items_text,
            cart_total=cart_total,
            extra_context=extra,
        )
        text = _provider_call(
            "You generate short spoken responses only — no formatting.",
            prompt,
            temperature=0.5,
        )
        text = text.strip().strip('"').strip("'")
    except Exception as exc:
        logger.warning("LLM NL response failed, using template: %s", exc)

    if not text:
        text = _fallback_template(stage, context)
    return text


def _fallback_template(stage: str, ctx: dict) -> str:
    """Return a template-based spoken response when LLM is unavailable."""
    items = ctx.get("items", [])
    item_count = len(items)

    if stage == "parse_ready":
        budget_text = ""
        if ctx.get("max_price"):
            budget_text = f"I'll keep it under ₹{ctx['max_price']}. "
        item_names = ", ".join(i.get("name", "") for i in items[:3])
        leftovers = item_count - 3
        if leftovers > 0:
            item_names += f" and {leftovers} more"
        return (f"Got it! I understood {item_count} items: {item_names}. "
                f"{budget_text}Shall I go ahead and search Zepto for these?")

    if stage == "progress_searching":
        return f"Looking for {ctx.get('item', 'your item')} on Zepto…"

    if stage == "progress_found_selected":
        return f"Found {ctx.get('item', 'it')} at ₹{ctx.get('price', '?')}. Adding the cheapest option to your cart."

    if stage == "progress_not_found":
        return f"Sorry, I couldn't find {ctx.get('item', 'that item')} on Zepto right now. Let me skip it."

    if stage == "cart_ready":
        items_summary = ", ".join(i.get("name", "") for i in items[:4])
        if len(items) > 4:
            items_summary += f" and {len(items) - 4} more"
        total = ctx.get("cart_total", 0)
        budget_check = ""
        if ctx.get("max_price") and float(total) <= float(ctx["max_price"]):
            budget_check = "It's within your budget. "
        return (f"Here's your cart: {items_summary}. "
                f"The total is ₹{total}. {budget_check}Shall I place the order?")

    if stage == "budget_exceeded":
        return (f"The cart total is ₹{ctx.get('cart_total', 0)}, "
                f"which is over your budget of ₹{ctx.get('max_price', 0)}. "
                "We'd need to remove some items or increase the budget.")

    if stage == "order_placed":
        return "Order placed successfully! Your Zepto delivery is on its way. Enjoy your goodies!"

    if stage == "cancelled":
        return "No problem, I've cancelled it. Let me know if you need anything else!"

    if stage == "error":
        return f"Sorry, something went wrong: {ctx.get('error', 'unknown error')}."

    if stage == "completed":
        return "All done! Your order has been placed. Happy eating!"

    return ctx.get("step", "") or "Working on it…"


def _items_summary(items: list) -> str:
    """Build a short text summary of items for the prompt context."""
    if not items:
        return "none yet"
    parts = []
    for i in items:
        name = i.get("name", i.get("text", "?"))
        price = i.get("price", "")
        if price:
            parts.append(f"{name} (₹{price})")
        else:
            parts.append(name)
    return "; ".join(parts[:6])

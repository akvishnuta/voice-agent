"""LLM-powered NL parsing and response generation.

Supports DeepSeek (default), OpenAI, and Anthropic providers.
"""

import json
import logging

from app.agent.prompts import PARSE_ORDER_SYSTEM_PROMPT, GENERATE_RESPONSE_PROMPT
from app.config import settings
from app.models.schemas import ParsedOrder, ParsedItem

logger = logging.getLogger(__name__)


# ── Helpers ────────────────────────────────────────────────────────────

def _build_user_prompt(text: str) -> str:
    prompt = f"Parse this voice command into a structured order:\n\n{text}"
    logger.debug("Built LLM user prompt (%d chars)", len(prompt))
    return prompt


def _try_parse_json(raw: str) -> dict:
    logger.debug("Raw LLM response (%d chars): %.200s", len(raw), raw)
    text = raw.strip()
    if text.startswith("```"):
        logger.debug("Stripping markdown code fences from response")
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    try:
        data = json.loads(text)
        logger.debug("JSON parsed successfully: %d top-level keys", len(data))
        return data
    except json.JSONDecodeError as exc:
        logger.error("Failed to parse LLM response as JSON: %s", exc)
        logger.error("Raw text after cleaning: %.500s", text)
        raise


def _json_to_parsed_order(data: dict) -> ParsedOrder:
    items = [ParsedItem(**it) for it in data.get("items", [])]
    logger.info("Parsed %d items: %s", len(items), [i.name for i in items])
    if data.get("max_price"):
        logger.info("Budget: ₹%s", data["max_price"])
    if data.get("preference"):
        logger.info("Preference: %s", data["preference"])
    return ParsedOrder(
        items=items,
        preference=data.get("preference", "cheapest"),
        max_price=data.get("max_price"),
        currency=data.get("currency", "INR"),
    )


# ── LLM providers ─────────────────────────────────────────────────────

def _chat_completion(api_key: str, system: str, user_msg: str,
                     base_url: str | None = None, temperature: float = 0.1) -> str:
    """Generic OpenAI-compatible chat completion returning raw text."""
    from openai import OpenAI

    provider_label = f"base_url={base_url}" if base_url else "OpenAI"
    logger.info("Calling %s | model=%s | temp=%.1f | system=%d chars | user=%d chars",
                provider_label, settings.llm_model, temperature,
                len(system), len(user_msg))

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

    content = resp.choices[0].message.content.strip()
    usage = resp.usage
    if usage:
        logger.debug("LLM usage — prompt=%d output=%d total=%d",
                     usage.prompt_tokens, usage.completion_tokens, usage.total_tokens)
    logger.debug("LLM response (%d chars): %.150s", len(content), content)
    return content


def _anthropic_completion(system: str, user_msg: str) -> str:
    from anthropic import Anthropic

    logger.info("Calling Anthropic | model=%s | system=%d chars | user=%d chars",
                settings.llm_model, len(system), len(user_msg))

    client = Anthropic(api_key=settings.anthropic_api_key)
    resp = client.messages.create(
        model=settings.llm_model,
        max_tokens=1024,
        system=system,
        messages=[{"role": "user", "content": user_msg}],
    )

    content = resp.content[0].text.strip()
    logger.debug("Anthropic response (%d chars): %.150s", len(content), content)
    return content


def _provider_call(system: str, user_msg: str, temperature: float = 0.1) -> str:
    provider = settings.llm_provider.lower()
    logger.debug("Provider dispatch: %s (model=%s)", provider, settings.llm_model)

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
    logger.info("=" * 60)
    logger.info("PARSE: input text (len=%d): %s", len(text), text[:200])

    if not text.strip():
        logger.error("Empty command text — raising ValueError")
        raise ValueError("Empty command text")

    raw = _provider_call(PARSE_ORDER_SYSTEM_PROMPT, _build_user_prompt(text))
    parsed = _json_to_parsed_order(_try_parse_json(raw))

    logger.info("PARSE result: %d items, pref=%s, max_price=%s",
                len(parsed.items), parsed.preference, parsed.max_price)
    logger.info("=" * 60)
    return parsed


def generate_nl_response(stage: str, context: dict) -> str:
    """Generate a natural-language spoken response for the current stage.

    Falls back to template-based responses if the LLM call fails.
    """
    logger.debug("NL response — stage=%s | context keys: %s", stage, list(context.keys()))

    text = ""
    user_cmd = context.get("user_command", "")
    items_text = _items_summary(context.get("items", []))
    cart_total = str(context.get("cart_total", 0))
    extra = ""

    if stage == "error":
        extra = f"- Error: {context.get('error', 'unknown')}"
        logger.warning("NL response for error stage: %s", context.get("error"))
    elif stage == "budget_exceeded":
        extra = f"- The user's max budget is ₹{context.get('max_price', 0)}"
        logger.warning("Budget exceeded: ₹%s > ₹%s",
                       context.get("cart_total", "?"), context.get("max_price", "?"))

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
        logger.info("NL response via LLM (%d chars): %s", len(text), text[:120])
    except Exception as exc:
        logger.warning("LLM NL response failed for stage '%s': %s — using fallback template", stage, exc)

    if not text:
        text = _fallback_template(stage, context)
        logger.info("NL response via template (%d chars): %s", len(text), text[:120])

    return text


def _fallback_template(stage: str, ctx: dict) -> str:
    """Return a template-based spoken response when LLM is unavailable."""
    items = ctx.get("items", [])
    item_count = len(items)

    logger.debug("Fallback template — stage=%s, item_count=%d", stage, item_count)

    if stage == "parse_ready":
        budget_text = ""
        if ctx.get("max_price"):
            budget_text = f"I will keep it under ₹{ctx['max_price']} Sir. "
        item_names = ", ".join(i.get("name", "") for i in items[:3])
        leftovers = item_count - 3
        if leftovers > 0:
            item_names += f" and {leftovers} more"
        return (f"Got it Sir! I understood {item_count} items: {item_names}. "
                f"{budget_text}Shall I go ahead and search Zepto for these?")

    if stage == "progress_searching":
        return f"Searching for {ctx.get('item', 'your item')} on Zepto Sir…"

    if stage == "progress_found_selected":
        return f"Found {ctx.get('item', 'it')} at ₹{ctx.get('price', '?')} Sir. Adding the cheapest option to your cart."

    if stage == "progress_not_found":
        return f"Sorry Sir, I could not find {ctx.get('item', 'that item')} on Zepto. Let me skip it."

    if stage == "cart_ready":
        items_summary = ", ".join(i.get("name", "") for i in items[:4])
        if len(items) > 4:
            items_summary += f" and {len(items) - 4} more"
        total = ctx.get("cart_total", 0)
        budget_check = ""
        if ctx.get("max_price") and float(total) <= float(ctx["max_price"]):
            budget_check = "It is within your budget Sir. "
        return (f"Here is your cart Sir: {items_summary}. "
                f"The total is ₹{total}. {budget_check}Shall I place the order?")

    if stage == "budget_exceeded":
        return (f"Sir, the cart total is ₹{ctx.get('cart_total', 0)}, "
                f"which is over your budget of ₹{ctx.get('max_price', 0)}. "
                "We would need to remove some items or increase the budget.")

    if stage == "order_placed":
        return "Order placed successfully Sir! Your Zepto delivery is on its way. Enjoy your goodies!"

    if stage == "dry_run_complete":
        return (
            "Dry run complete Sir! Items have been added to your Zepto cart "
            "but no order was placed. You can review the cart in the browser "
            "and checkout manually if you like."
        )

    if stage == "cancelled":
        return "No problem Sir, I have cancelled it. Let me know if you need anything else!"

    if stage == "error":
        return f"Sorry Sir, something went wrong: {ctx.get('error', 'unknown error')}."

    if stage == "completed":
        return "All done Sir! Your order has been placed. Happy eating!"

    if stage == "otp_requested":
        return "Sir, I have sent an OTP to your phone. Please enter the 4 to 6 digit code here."

    logger.warning("Unrecognised fallback stage '%s' — returning step text", stage)
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

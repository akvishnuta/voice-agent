"""Playwright-based browser automation client for Zepto.

Selector notes (Zepto web-app may change — update SELECTORS if things break):
- Login uses a phone-number input followed by an OTP screen.
- The search bar is typically an <input> with placeholder containing "Search".
- Product cards have item name, price, and an "Add" / "+" button.
"""

import json
import logging
from pathlib import Path

from playwright.async_api import async_playwright, Browser, Page

from app.config import settings
from app.models.schemas import ProductResult

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════
#  CSS / XPath selectors  —  update these if the Zepto site changes
# ═══════════════════════════════════════════════════════════════════════
SELECTORS = {
    # Login
    "phone_input": 'input[type="tel"], input[placeholder*="phone" i], input[placeholder*="mobile" i]',
    "otp_input": 'input[type="tel"][maxlength="1"], input[placeholder*="OTP" i], input[placeholder*="code" i]',
    "login_submit": 'button:has-text("Continue"), button:has-text("Login"), button:has-text("Send OTP")',
    # After-login landing
    "logged_in_indicator": '[data-testid="user-icon"], [data-testid="profile-icon"], a[href*="account"]',
    # Search
    "search_input": 'input[placeholder*="Search" i], input[type="search"], [data-testid="search-input"] input',
    "search_submit": 'button:has-text("Search"), button[type="submit"]',
    # Product listing
    "product_card": '[data-testid="product-card"], [class*="productCard"], [class*="ProductCard"]',
    "product_name": '[class*="productName"] h4, [class*="productName"] span, [class*="title"]',
    "product_price": '[class*="price"], [class*="Price"], span[class*="final"]',
    "product_add_btn": 'button:has-text("Add"), button:has-text("+"), [aria-label="Add"]',
    "product_quantity_label": '[class*="quantity"], [class*="weight"], [class*="Volume"], [class*="Size"]',
    # Cart
    "cart_icon": '[data-testid="cart-icon"], a[href*="cart"], [class*="cartIcon"], [aria-label*="Cart"]',
    "cart_items": '[data-testid="cart-item"], [class*="cartItem"], [class*="CartItem"]',
    "cart_total": '[data-testid="cart-total"], [class*="cartTotal"], [class*="totalAmount"], [class*="Total"]',
    "checkout_btn": 'button:has-text("Checkout"), button:has-text("Place Order"), button:has-text("Proceed")',
}


class ZeptoSessionError(Exception):
    """Recoverable error during Zepto automation."""


class ZeptoClient:
    """Manages a Playwright browser session logged into Zepto."""

    def __init__(self):
        self._playwright = None
        self._browser: Browser | None = None
        self._page: Page | None = None
        self._cookies_path: Path = Path(settings.playwright_cookies_path)

    # ── Lifecycle ──────────────────────────────────────────────────

    async def start(self) -> None:
        """Launch browser (headed unless configured otherwise)."""
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=settings.browser_headless,
        )
        self._page = await self._browser.new_page()
        self._page.set_default_timeout(15_000)

    async def close(self) -> None:
        """Close browser and clean up."""
        if self._page:
            await self._page.close()
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()

    async def __aenter__(self):
        await self.start()
        return self

    async def __aexit__(self, *args):
        await self.close()

    # ── Login ──────────────────────────────────────────────────────

    async def login(self, phone: str | None = None) -> None:
        """Log into Zepto.

        Attempts cookie restoration first. If that fails, walks the user
        through the OTP login flow by showing the browser.
        """
        page = self._page
        phone = phone or settings.zepto_phone

        # 1. Try restoring saved cookies
        if await self._try_restore_cookies():
            logger.info("Session restored from saved cookies")
            return

        # 2. Full login flow
        await page.goto(settings.zepto_url, wait_until="domcontentloaded")
        await self._wait_and_fill(phone)
        await self._wait_for_otp()
        await self._save_cookies()
        logger.info("Login successful, cookies saved")

    async def _try_restore_cookies(self) -> bool:
        """Navigate and inject saved cookies. Return True if logged in."""
        if not self._cookies_path.exists():
            return False
        try:
            cookies = json.loads(self._cookies_path.read_text())
            page = self._page
            # Navigate first so the domain matches
            await page.goto(settings.zepto_url, wait_until="domcontentloaded")
            await page.context.add_cookies(cookies)
            await page.reload(wait_until="domcontentloaded")
            # Check for a logged-in indicator
            logged_in = await page.locator(SELECTORS["logged_in_indicator"]).first.is_visible()
            if logged_in:
                return True
            logger.warning("Saved cookies expired, re-logging in")
        except Exception as exc:
            logger.warning("Could not restore cookies: %s", exc)
        return False

    async def _wait_and_fill(self, phone: str) -> None:
        """Enter phone number and submit."""
        page = self._page
        await page.wait_for_selector(SELECTORS["phone_input"], state="attached")
        await page.locator(SELECTORS["phone_input"]).first.fill(phone or "")
        submit = page.locator(SELECTORS["login_submit"]).first
        if await submit.is_visible():
            await submit.click()

    async def _wait_for_otp(self) -> None:
        """Wait for the user to enter the OTP in the headed browser."""
        page = self._page
        logger.info("Waiting for OTP entry in browser…")
        try:
            # Wait for navigation away from login screen (up to 120 s)
            await page.wait_for_url(
                f"{settings.zepto_url}/**",
                timeout=120_000,
            )
        except Exception:
            # Fallback: wait for the logged-in indicator to appear
            await page.wait_for_selector(
                SELECTORS["logged_in_indicator"],
                state="visible",
                timeout=120_000,
            )
        logger.info("OTP verified")

    async def _save_cookies(self) -> None:
        """Persist browser cookies to disk."""
        cookies = await self._page.context.cookies()
        self._cookies_path.write_text(json.dumps(cookies, indent=2))
        logger.info("Cookies saved to %s", self._cookies_path)

    # ── Shopping operations ────────────────────────────────────────

    async def search(self, query: str) -> list[ProductResult]:
        """Search Zepto for *query* and return parsed product results.

        Products are returned sorted by price ascending so the caller can
        pick the cheapest.
        """
        page = self._page
        search_input = page.locator(SELECTORS["search_input"]).first
        await search_input.fill(query)
        await search_input.press("Enter")
        await page.wait_for_timeout(3_000)  # let results render

        products: list[ProductResult] = []
        cards = page.locator(SELECTORS["product_card"])

        count = await cards.count()
        for i in range(count):
            card = cards.nth(i)
            try:
                name = await card.locator(SELECTORS["product_name"]).first.inner_text()
                price_text = await card.locator(SELECTORS["product_price"]).first.inner_text()
                price = self._parse_price(price_text)
                qty = ""
                if await card.locator(SELECTORS["product_quantity_label"]).first.is_visible():
                    qty = await card.locator(SELECTORS["product_quantity_label"]).first.inner_text()
                products.append(
                    ProductResult(
                        name=name.strip(),
                        price=price,
                        quantity_label=qty.strip(),
                    )
                )
            except Exception as exc:
                logger.debug("Skipping product card %d: %s", i, exc)
                continue

        # Sort by price ascending so the cheapest is first
        products.sort(key=lambda p: p.price)
        logger.info("Found %d products for '%s'", len(products), query)
        return products

    async def add_to_cart(self, product: ProductResult) -> None:
        """Click the Add button for *product*."""
        page = self._page
        cards = page.locator(SELECTORS["product_card"])
        count = await cards.count()
        for i in range(count):
            card = cards.nth(i)
            try:
                name = await card.locator(SELECTORS["product_name"]).first.inner_text()
                if product.name.strip().lower() in name.strip().lower():
                    add_btn = card.locator(SELECTORS["product_add_btn"]).first
                    await add_btn.click()
                    await page.wait_for_timeout(1_000)
                    logger.info("Added '%s' to cart", product.name)
                    return
            except Exception as exc:
                logger.debug("add_to_cart error on card %d: %s", i, exc)
                continue
        logger.warning("Could not find product '%s' in results to click Add", product.name)

    async def get_cart(self) -> dict:
        """Navigate to cart and return items + total."""
        page = self._page
        cart_btn = page.locator(SELECTORS["cart_icon"]).first
        if await cart_btn.is_visible():
            await cart_btn.click()
            await page.wait_for_timeout(2_000)

        items: list[dict] = []
        total: float | None = None

        cart_item_elements = page.locator(SELECTORS["cart_items"])
        count = await cart_item_elements.count()
        for i in range(count):
            el = cart_item_elements.nth(i)
            try:
                text = await el.inner_text()
                items.append({"text": text.strip()})
            except Exception:
                continue

        total_el = page.locator(SELECTORS["cart_total"]).first
        if await total_el.is_visible():
            raw = await total_el.inner_text()
            total = self._parse_price(raw)

        return {"items": items, "total": total}

    async def checkout(self) -> None:
        """Click the checkout / place-order button."""
        page = self._page
        btn = page.locator(SELECTORS["checkout_btn"]).first
        if await btn.is_visible():
            await btn.click()
            await page.wait_for_timeout(3_000)
            logger.info("Checkout initiated")
        else:
            logger.warning("Checkout button not found — may need selector update")

    @staticmethod
    def _parse_price(text: str) -> float:
        """Strip currency symbols and parse to float."""
        cleaned = text.replace("₹", "").replace(",", "").replace(" ", "").strip()
        try:
            return float(cleaned)
        except ValueError:
            return 0.0

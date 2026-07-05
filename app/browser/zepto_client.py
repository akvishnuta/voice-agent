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
        logger.info("Launching Playwright browser (headless=%s)…", settings.browser_headless)
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=settings.browser_headless,
        )
        self._page = await self._browser.new_page()
        self._page.set_default_timeout(15_000)
        logger.info("Browser launched, page created")

    async def close(self) -> None:
        """Close browser and clean up."""
        logger.debug("Closing browser…")
        if self._page:
            await self._page.close()
            logger.debug("Page closed")
        if self._browser:
            await self._browser.close()
            logger.debug("Browser closed")
        if self._playwright:
            await self._playwright.stop()
            logger.debug("Playwright stopped")
        logger.info("Browser session closed")

    async def __aenter__(self):
        await self.start()
        return self

    async def __aexit__(self, *args):
        await self.close()

    # ── Login ──────────────────────────────────────────────────────

    async def login(self, phone: str | None = None) -> bool:
        """Log into Zepto.

        Returns ``True`` if already logged in via saved cookies (no OTP needed).
        Returns ``False`` if an OTP was sent — caller must then call
        :meth:`submit_otp` with the code.
        """
        page = self._page
        phone = phone or settings.zepto_phone
        logger.info("Login: phone=%s", phone)

        # 1. Try restoring saved cookies
        if await self._try_restore_cookies():
            logger.info("Session restored from saved cookies — login skipped")
            return True

        # 2. Full login flow — send OTP
        logger.info("No valid cookies — navigating to Zepto login")
        await page.goto(settings.zepto_url, wait_until="domcontentloaded")
        logger.info("Navigated to %s", settings.zepto_url)
        await self._wait_and_fill(phone)
        logger.info("OTP sent — caller must submit_otp() with the code")
        return False

    async def submit_otp(self, otp: str) -> None:
        """Fill the OTP input fields and wait for login to complete.

        Call this after :meth:`login` returns ``False``.
        """
        page = self._page
        digits = list(otp.strip())
        logger.info("Filling %d OTP digits into browser…", len(digits))

        otp_inputs = page.locator(SELECTORS["otp_input"])
        field_count = await otp_inputs.count()
        logger.debug("Found %d OTP input fields", field_count)

        for i, digit in enumerate(digits[:field_count]):
            await otp_inputs.nth(i).fill(digit)
            logger.debug("OTP field %d: filled '%s'", i, digit)

        if field_count == 0:
            # Single-field OTP input (not split into individual boxes)
            logger.debug("No split OTP fields — trying single input")
            single = page.locator(SELECTORS["otp_input"]).first
            if await single.is_visible():
                await single.fill(otp.strip())

        # Wait for login to complete
        logger.info("Waiting for login to complete…")
        try:
            await page.wait_for_selector(
                SELECTORS["logged_in_indicator"],
                state="visible",
                timeout=30_000,
            )
            logger.info("Login confirmed — logged-in indicator visible")
        except Exception:
            # Fallback: wait for URL to change from login page
            logger.debug("Logged-in indicator not found — waiting for URL change")
            await page.wait_for_url(
                f"{settings.zepto_url}/**",
                timeout=30_000,
            )

        await self._save_cookies()
        logger.info("OTP login complete — cookies saved")

    async def _try_restore_cookies(self) -> bool:
        """Navigate and inject saved cookies. Return True if logged in."""
        if not self._cookies_path.exists():
            logger.debug("No saved cookies file at %s", self._cookies_path)
            return False
        try:
            cookie_count = len(json.loads(self._cookies_path.read_text()))
            logger.debug("Found cookie file with %d cookies", cookie_count)
            cookies = json.loads(self._cookies_path.read_text())
            page = self._page
            await page.goto(settings.zepto_url, wait_until="domcontentloaded")
            await page.context.add_cookies(cookies)
            await page.reload(wait_until="domcontentloaded")
            logged_in = await page.locator(SELECTORS["logged_in_indicator"]).first.is_visible()
            if logged_in:
                logger.info("Cookies valid — logged into Zepto")
                return True
            logger.warning("Cookies expired — re-login required")
        except Exception as exc:
            logger.warning("Cookie restore error: %s", exc)
        return False

    async def _wait_and_fill(self, phone: str) -> None:
        """Enter phone number and submit."""
        page = self._page
        logger.debug("Waiting for phone input field…")
        await page.wait_for_selector(SELECTORS["phone_input"], state="attached")
        logger.debug("Filling phone: %s", phone)
        await page.locator(SELECTORS["phone_input"]).first.fill(phone or "")
        submit = page.locator(SELECTORS["login_submit"]).first
        if await submit.is_visible():
            logger.debug("Clicking login submit button")
            await submit.click()
        else:
            logger.debug("Login submit button not found (may have auto-submitted)")

    async def _save_cookies(self) -> None:
        """Persist browser cookies to disk."""
        cookies = await self._page.context.cookies()
        self._cookies_path.write_text(json.dumps(cookies, indent=2))
        logger.info("%d cookies saved to %s", len(cookies), self._cookies_path)

    # ── Shopping operations ────────────────────────────────────────

    async def search(self, query: str) -> list[ProductResult]:
        """Search Zepto for *query* and return parsed product results.

        Products are returned sorted by price ascending so the caller can
        pick the cheapest.
        """
        page = self._page
        logger.info('Search: typing "%s" and pressing Enter…', query)
        search_input = page.locator(SELECTORS["search_input"]).first
        await search_input.fill(query)
        await search_input.press("Enter")
        await page.wait_for_timeout(3_000)

        products: list[ProductResult] = []
        cards = page.locator(SELECTORS["product_card"])

        count = await cards.count()
        logger.info('Search results for "%s": %d product cards found', query, count)

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
                logger.debug("  [%d] '%s' — ₹%.2f (%s)", i, name.strip(), price, qty.strip())
            except Exception as exc:
                logger.debug("Skipping product card %d: %s", i, exc)
                continue

        products.sort(key=lambda p: p.price)
        if products:
            logger.info("Cheapest result: '%s' at ₹%.2f", products[0].name, products[0].price)
            logger.info("Price range: ₹%.2f – ₹%.2f (%d products)",
                        products[0].price, products[-1].price, len(products))
        else:
            logger.warning('No products found for "%s"', query)
        return products

    async def add_to_cart(self, product: ProductResult) -> None:
        """Click the Add button for *product*."""
        page = self._page
        logger.info('Add to cart: looking for "%s" in product cards…', product.name)
        cards = page.locator(SELECTORS["product_card"])
        count = await cards.count()
        for i in range(count):
            card = cards.nth(i)
            try:
                name = await card.locator(SELECTORS["product_name"]).first.inner_text()
                if product.name.strip().lower() in name.strip().lower():
                    add_btn = card.locator(SELECTORS["product_add_btn"]).first
                    logger.info('Clicking "Add" for "%s" (matching card name "%s")',
                                product.name, name.strip())
                    await add_btn.click()
                    await page.wait_for_timeout(1_000)
                    logger.info("✅ Added to cart: %s", product.name)
                    return
            except Exception as exc:
                logger.debug("add_to_cart error on card %d: %s", i, exc)
                continue
        logger.warning('❌ Could not find product card for "%s" — Add button not clicked',
                       product.name)

    async def get_cart(self) -> dict:
        """Navigate to cart and return items + total."""
        page = self._page
        logger.info("Opening cart…")
        cart_btn = page.locator(SELECTORS["cart_icon"]).first
        if await cart_btn.is_visible():
            logger.debug("Clicking cart icon")
            await cart_btn.click()
            await page.wait_for_timeout(2_000)
        else:
            logger.debug("Cart icon not visible — trying to read items from current page")

        items: list[dict] = []
        total: float | None = None

        cart_item_elements = page.locator(SELECTORS["cart_items"])
        count = await cart_item_elements.count()
        logger.info("Cart items found: %d", count)
        for i in range(count):
            el = cart_item_elements.nth(i)
            try:
                text = await el.inner_text()
                items.append({"text": text.strip()})
                logger.debug("  Cart item %d: %s", i, text.strip()[:80])
            except Exception:
                continue

        total_el = page.locator(SELECTORS["cart_total"]).first
        if await total_el.is_visible():
            raw = await total_el.inner_text()
            total = self._parse_price(raw)
            logger.info("Cart total from page: ₹%s (raw: '%s')", total, raw.strip())
        else:
            logger.warning("Cart total element not found")

        return {"items": items, "total": total}

    async def checkout(self) -> None:
        """Click the checkout / place-order button."""
        page = self._page
        logger.info("Looking for checkout button…")
        btn = page.locator(SELECTORS["checkout_btn"]).first
        if await btn.is_visible():
            logger.info("Clicking checkout button")
            await btn.click()
            await page.wait_for_timeout(3_000)
            logger.info("✅ Checkout initiated successfully")
        else:
            logger.warning("❌ Checkout button not found — may need selector update")

    @staticmethod
    def _parse_price(text: str) -> float:
        """Strip currency symbols and parse to float."""
        cleaned = text.replace("₹", "").replace(",", "").replace(" ", "").strip()
        try:
            result = float(cleaned)
            logger.debug("Parsed price: '%s' → %.2f", text, result)
            return result
        except ValueError:
            logger.warning("Could not parse price from '%s'", text)
            return 0.0

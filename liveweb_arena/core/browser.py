"""Browser engine with session isolation for concurrent evaluations"""

import asyncio
from typing import Optional
from playwright.async_api import async_playwright, Browser, BrowserContext, Page, Playwright

from .models import BrowserObservation, BrowserAction

# Constants
MAX_ACCESSIBILITY_TREE_LENGTH = 20000
PAGE_TIMEOUT_MS = 30000
NAVIGATION_TIMEOUT_MS = 30000


class BrowserSession:
    """
    Isolated browser session (context + page).
    Each evaluate() call creates a new session to avoid state interference.

    In strict isolation mode, the session owns its own browser instance.
    """

    def __init__(self, context: BrowserContext, page: Page, browser: Browser = None):
        self._context = context
        self._page = page
        self._browser = browser  # Only set in strict isolation mode

    async def goto(self, url: str) -> BrowserObservation:
        """Navigate to URL and return observation"""
        try:
            await self._page.goto(url, wait_until="domcontentloaded", timeout=NAVIGATION_TIMEOUT_MS)
            # Wait a bit for dynamic content
            await self._page.wait_for_load_state("networkidle", timeout=10000)
        except Exception:
            # Network idle timeout is acceptable, page may still be usable
            pass
        return await self._get_observation()

    async def execute_action(self, action: BrowserAction) -> BrowserObservation:
        """Execute browser action and return new observation"""
        action_type = action.action_type
        params = action.params

        try:
            if action_type == "goto":
                url = params.get("url", "")
                await self._page.goto(url, wait_until="domcontentloaded", timeout=NAVIGATION_TIMEOUT_MS)
                try:
                    await self._page.wait_for_load_state("networkidle", timeout=10000)
                except Exception:
                    pass

            elif action_type == "click":
                selector = params.get("selector", "")
                timeout_ms = params.get("timeout_ms", 5000)
                await self._page.click(selector, timeout=timeout_ms)
                # Wait briefly for potential navigation
                await asyncio.sleep(0.3)

            elif action_type == "type":
                selector = params.get("selector", "")
                text = params.get("text", "")
                press_enter = params.get("press_enter", False)
                await self._page.fill(selector, text)
                if press_enter:
                    await self._page.press(selector, "Enter")
                    # Wait briefly for potential navigation after Enter
                    await asyncio.sleep(0.3)

            elif action_type == "press":
                key = params.get("key", "Enter")
                await self._page.keyboard.press(key)
                # Wait briefly for potential navigation
                await asyncio.sleep(0.3)

            elif action_type == "scroll":
                direction = params.get("direction", "down")
                amount = params.get("amount", 300)
                delta = amount if direction == "down" else -amount
                await self._page.mouse.wheel(0, delta)

            elif action_type == "wait":
                seconds = params.get("seconds", 1)
                await asyncio.sleep(seconds)

            elif action_type == "click_role":
                role = params.get("role", "button")
                name = params.get("name", "")
                exact = params.get("exact", False)
                locator = self._page.get_by_role(role, name=name, exact=exact)
                await locator.click(timeout=5000)
                # Wait briefly for potential navigation
                await asyncio.sleep(0.3)

            elif action_type == "type_role":
                role = params.get("role", "textbox")
                name = params.get("name", "")
                text = params.get("text", "")
                press_enter = params.get("press_enter", False)
                locator = self._page.get_by_role(role, name=name)
                await locator.fill(text)
                if press_enter:
                    await locator.press("Enter")
                    # Wait briefly for potential navigation
                    await asyncio.sleep(0.3)

            elif action_type == "stop":
                # Stop action - no browser operation needed
                pass

            else:
                # Unknown action type - treat as wait
                await asyncio.sleep(0.5)

        except Exception as e:
            # Log error but continue - observation will show current state
            pass

        return await self._get_observation()

    async def _get_observation(self, max_retries: int = 3) -> BrowserObservation:
        """Get current browser observation with retry logic for navigation timing"""
        for attempt in range(max_retries):
            try:
                # Wait for page to be in a stable state
                try:
                    await self._page.wait_for_load_state("domcontentloaded", timeout=5000)
                except Exception:
                    pass  # Page might already be loaded or timeout is acceptable

                url = self._page.url
                title = await self._page.title()

                # Get accessibility tree (truncated)
                a11y_tree = ""
                try:
                    a11y_snapshot = await self._page.accessibility.snapshot()
                    if a11y_snapshot:
                        a11y_tree = self._format_accessibility_tree(a11y_snapshot)
                except Exception:
                    pass

                # If accessibility tree is empty or too short, get page text content
                # This handles sites like wttr.in that use <pre> tags and ASCII art
                page_text = ""
                if len(a11y_tree.strip()) < 100:
                    try:
                        # Get visible text content from the page
                        page_text = await self._page.evaluate("""
                            () => {
                                // Try to get text from pre elements first (for ASCII art sites)
                                const preElements = document.querySelectorAll('pre');
                                if (preElements.length > 0) {
                                    return Array.from(preElements).map(el => el.innerText).join('\\n');
                                }
                                // Fall back to body text
                                return document.body.innerText || '';
                            }
                        """)
                    except Exception:
                        pass

                # Combine accessibility tree and page text
                content = ""
                if a11y_tree.strip():
                    content = a11y_tree
                if page_text.strip():
                    if content:
                        content += "\n\n--- Page Text Content ---\n" + page_text
                    else:
                        content = page_text

                # Truncate if too long
                if len(content) > MAX_ACCESSIBILITY_TREE_LENGTH:
                    content = content[:MAX_ACCESSIBILITY_TREE_LENGTH] + "\n... (truncated)"

                return BrowserObservation(
                    url=url,
                    title=title,
                    accessibility_tree=content,
                )

            except Exception as e:
                # Execution context destroyed - page is navigating
                if attempt < max_retries - 1:
                    # Wait a bit and retry
                    await asyncio.sleep(0.5)
                    continue
                else:
                    # Final attempt failed - return minimal observation
                    return BrowserObservation(
                        url=self._page.url if self._page else "",
                        title="",
                        accessibility_tree="",
                    )

    def _format_accessibility_tree(self, node: dict, indent: int = 0) -> str:
        """Format accessibility tree node recursively"""
        if not node:
            return ""

        lines = []
        prefix = "  " * indent

        role = node.get("role", "")
        name = node.get("name", "")
        value = node.get("value", "")

        # Build node representation
        parts = [role]
        if name:
            parts.append(f'"{name}"')
        if value:
            parts.append(f'value="{value}"')

        lines.append(f"{prefix}{' '.join(parts)}")

        # Process children
        children = node.get("children", [])
        for child in children:
            lines.append(self._format_accessibility_tree(child, indent + 1))

        return "\n".join(lines)

    async def close(self):
        """Close session (context, page, and browser if in strict mode)"""
        try:
            await self._page.close()
        except Exception:
            pass
        try:
            await self._context.close()
        except Exception:
            pass
        # In strict isolation mode, also close the browser instance
        if self._browser is not None:
            try:
                await self._browser.close()
            except Exception:
                pass


class BrowserEngine:
    """
    Browser engine that manages Playwright and Browser instances.

    Supports two isolation modes:
    - shared: Single browser instance, isolated contexts (default, faster)
    - strict: Separate browser instance per session (stronger isolation)
    """

    def __init__(self, headless: bool = True, isolation_mode: str = "shared"):
        """
        Initialize browser engine.

        Args:
            headless: Run browser in headless mode
            isolation_mode: "shared" (default) or "strict"
                - shared: Single browser, separate contexts (faster, good for most cases)
                - strict: Separate browser per session (stronger isolation, slower)
        """
        self._headless = headless
        self._isolation_mode = isolation_mode
        self._playwright: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._lock = asyncio.Lock()
        self._browser_args = [
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
        ]

    async def start(self):
        """Start Playwright and launch browser (for shared mode)"""
        async with self._lock:
            if self._playwright is None:
                self._playwright = await async_playwright().start()

            if self._isolation_mode == "shared" and self._browser is None:
                self._browser = await self._playwright.chromium.launch(
                    headless=self._headless,
                    args=self._browser_args,
                )

    async def new_session(self) -> BrowserSession:
        """Create a new isolated browser session"""
        if self._playwright is None:
            await self.start()

        if self._isolation_mode == "strict":
            # Strict mode: create a new browser instance for each session
            browser = await self._playwright.chromium.launch(
                headless=self._headless,
                args=self._browser_args,
            )
            context = await browser.new_context(
                viewport={"width": 1280, "height": 720},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                # Additional isolation options
                ignore_https_errors=False,
                java_script_enabled=True,
                bypass_csp=False,
            )
            context.set_default_timeout(PAGE_TIMEOUT_MS)
            page = await context.new_page()
            return BrowserSession(context, page, browser=browser)
        else:
            # Shared mode: use shared browser with isolated context
            if self._browser is None:
                await self.start()

            context = await self._browser.new_context(
                viewport={"width": 1280, "height": 720},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                # Context-level isolation options
                ignore_https_errors=False,
                java_script_enabled=True,
                bypass_csp=False,
            )
            context.set_default_timeout(PAGE_TIMEOUT_MS)
            page = await context.new_page()
            return BrowserSession(context, page)

    async def stop(self):
        """Stop browser and Playwright with timeout"""
        try:
            # 使用超时避免无限等待锁
            async with asyncio.timeout(5):
                async with self._lock:
                    if self._browser:
                        try:
                            await asyncio.wait_for(self._browser.close(), timeout=3)
                        except Exception:
                            pass
                        self._browser = None

                    if self._playwright:
                        try:
                            await asyncio.wait_for(self._playwright.stop(), timeout=3)
                        except Exception:
                            pass
                        self._playwright = None
        except asyncio.TimeoutError:
            # 超时则强制清理引用
            self._browser = None
            self._playwright = None

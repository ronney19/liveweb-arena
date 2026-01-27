"""Browser engine with session isolation for concurrent evaluations"""

import asyncio
from typing import Optional, TYPE_CHECKING
from playwright.async_api import async_playwright, Browser, BrowserContext, Page, Playwright

from .models import BrowserObservation, BrowserAction

if TYPE_CHECKING:
    from .request_interceptor import RequestInterceptor

# Constants
MAX_CONTENT_LENGTH = 20000  # Max content shown per view
VIEW_MORE_OVERLAP = 2000    # Overlap between views for context continuity
PAGE_TIMEOUT_MS = 30000
NAVIGATION_TIMEOUT_MS = 30000


class BrowserSession:
    """
    Isolated browser session (context + page).
    Each evaluate() call creates a new session to avoid state interference.

    In strict isolation mode, the session owns its own browser instance.
    """

    # Step size for view_more = viewport size minus overlap
    VIEW_STEP = MAX_CONTENT_LENGTH - VIEW_MORE_OVERLAP

    def __init__(
        self,
        context: BrowserContext,
        page: Page,
        browser: Browser = None,
    ):
        self._context = context
        self._page = page
        self._browser = browser  # Only set in strict isolation mode
        # Virtual scroll state for handling truncated content
        self._view_offset = 0
        self._last_full_content = ""
        self._last_url = ""
        self._blocked_patterns = []
        self._allowed_domains = None  # None means allow all
        self._snapshot_interceptor: Optional["RequestInterceptor"] = None

    async def set_allowed_domains(self, domains: list):
        """
        Set whitelist of allowed domains.

        Only requests to these domains will be allowed. All other requests
        will be blocked. This prevents agents from cheating by visiting
        external websites, search engines, or AI services.

        Note: If snapshot interceptor is set, it handles all routing including
        domain filtering. This method is only used when no interceptor is active.

        Args:
            domains: List of allowed domain names (without protocol)
                    Example: ["wttr.in", "coingecko.com"]
        """
        from urllib.parse import urlparse

        self._allowed_domains = set(d.lower() for d in domains)

        # If snapshot interceptor is set, it handles routing
        if self._snapshot_interceptor:
            return

        session = self  # Capture reference for closure

        async def check_domain(route):
            url = route.request.url

            # Always allow about:blank
            if url == "about:blank" or url.startswith("about:"):
                await route.continue_()
                return

            try:
                parsed = urlparse(url)
                domain = parsed.netloc.lower()
                # Remove port if present
                if ":" in domain:
                    domain = domain.split(":")[0]

                # Check if domain or any parent domain is allowed
                is_allowed = False
                for allowed in session._allowed_domains:
                    if domain == allowed or domain.endswith("." + allowed):
                        is_allowed = True
                        break

                if not is_allowed:
                    await route.abort("blockedbyclient")
                    return

                await route.continue_()

            except Exception:
                await route.abort("blockedbyclient")

        # Intercept all requests
        await self._context.route("**/*", check_domain)

    async def block_urls(self, patterns: list):
        """
        Block URLs matching the given patterns.

        Uses Playwright's route interception to abort requests to blocked URLs.
        This forces agents to use actual websites instead of APIs.

        Args:
            patterns: List of URL patterns (supports * wildcard)
                     Example: ["*api.example.com*"]
        """
        self._blocked_patterns.extend(patterns)
        for pattern in patterns:
            await self._context.route(pattern, lambda route: route.abort())

    async def set_snapshot_interceptor(self, interceptor: "RequestInterceptor"):
        """
        Set up snapshot-based request interception.

        This replaces HAR-based caching with direct request interception
        using the atomic snapshot cache.

        Args:
            interceptor: RequestInterceptor instance configured with a Snapshot
        """
        from liveweb_arena.core.request_interceptor import RequestInterceptor
        self._snapshot_interceptor = interceptor

        # Route all requests through the interceptor
        await self._context.route("**/*", interceptor.handle_route)

    def get_interceptor_stats(self) -> Optional[dict]:
        """Get request interception statistics."""
        if hasattr(self, '_snapshot_interceptor') and self._snapshot_interceptor:
            return self._snapshot_interceptor.get_stats()
        return None

    async def goto(self, url: str, max_retries: int = 3) -> BrowserObservation:
        """Navigate to URL and return observation with automatic retry on failure"""
        # Reset view offset when navigating to a new page
        self._view_offset = 0
        self._last_full_content = ""

        for attempt in range(max_retries):
            try:
                await self._page.goto(url, wait_until="domcontentloaded", timeout=NAVIGATION_TIMEOUT_MS)
                # Wait a bit for dynamic content
                await self._page.wait_for_load_state("networkidle", timeout=10000)
            except Exception:
                # Network idle timeout is acceptable, page may still be usable
                pass
            # Check if navigation failed
            current_url = self._page.url
            if not current_url.startswith("chrome-error://"):
                break  # Navigation succeeded
            # Wait before retry
            if attempt < max_retries - 1:
                await asyncio.sleep(1.0 * (attempt + 1))
        return await self._get_observation()

    async def execute_action(self, action: BrowserAction, max_nav_retries: int = 3) -> BrowserObservation:
        """Execute browser action and return new observation"""
        action_type = action.action_type
        params = action.params

        try:
            if action_type == "goto":
                url = params.get("url", "")
                # Retry navigation if it fails (chrome-error://)
                for nav_attempt in range(max_nav_retries):
                    await self._page.goto(url, wait_until="domcontentloaded", timeout=NAVIGATION_TIMEOUT_MS)
                    try:
                        await self._page.wait_for_load_state("networkidle", timeout=10000)
                    except Exception:
                        pass
                    # Check if navigation failed
                    current_url = self._page.url
                    if not current_url.startswith("chrome-error://"):
                        break  # Navigation succeeded
                    # Wait before retry
                    if nav_attempt < max_nav_retries - 1:
                        await asyncio.sleep(1.0 * (nav_attempt + 1))

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

            elif action_type == "view_more":
                # Virtual scrolling for truncated content - doesn't scroll the actual page
                direction = params.get("direction", "down")
                if direction == "down":
                    self._view_offset += self.VIEW_STEP
                else:
                    self._view_offset = max(0, self._view_offset - self.VIEW_STEP)

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

    async def get_observation(self, max_retries: int = 3) -> BrowserObservation:
        """Get current browser observation with retry logic for navigation timing"""
        return await self._get_observation(max_retries)

    async def _get_observation(self, max_retries: int = 5) -> BrowserObservation:
        """Get current browser observation with retry logic for page loading"""
        for attempt in range(max_retries):
            try:
                url = self._page.url

                # Check for error pages - no need to wait
                if url.startswith("chrome-error://") or url.startswith("about:neterror"):
                    return BrowserObservation(
                        url=url,
                        title="Error",
                        accessibility_tree="[Page failed to load - network error]",
                    )

                # Wait for page to be fully loaded (network idle = no pending requests)
                page_loaded = False
                try:
                    await self._page.wait_for_load_state("networkidle", timeout=10000)
                    page_loaded = True
                except Exception:
                    # Network idle timeout - page might still be loading
                    # Try domcontentloaded as fallback
                    try:
                        await self._page.wait_for_load_state("domcontentloaded", timeout=3000)
                        page_loaded = True
                    except Exception:
                        pass

                # If page not loaded and we have retries left, wait and retry
                if not page_loaded and attempt < max_retries - 1:
                    await asyncio.sleep(1.0)
                    continue

                title = await self._page.title()

                # Get accessibility tree
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
                full_content = ""
                if a11y_tree.strip():
                    full_content = a11y_tree
                if page_text.strip():
                    if full_content:
                        full_content += "\n\n--- Page Text Content ---\n" + page_text
                    else:
                        full_content = page_text

                # Store full content and check if URL changed (reset offset if so)
                if url != self._last_url:
                    self._view_offset = 0
                    self._last_url = url
                self._last_full_content = full_content

                # Apply virtual scrolling with view window
                total_len = len(full_content)
                if total_len > MAX_CONTENT_LENGTH:
                    # Clamp view offset to valid range
                    max_offset = max(0, total_len - MAX_CONTENT_LENGTH)
                    self._view_offset = min(self._view_offset, max_offset)

                    # Extract window of content
                    start = self._view_offset
                    end = min(start + MAX_CONTENT_LENGTH, total_len)
                    content = full_content[start:end]

                    # Add position indicators
                    position_info = []
                    if start > 0:
                        position_info.append(f"... (content above, use view_more direction=up to see)")
                    if end < total_len:
                        position_info.append(f"... (content below, use view_more direction=down to see)")

                    if position_info:
                        content = "\n".join(position_info[:1]) + "\n" + content
                        if len(position_info) > 1:
                            content += "\n" + position_info[1]
                else:
                    # Content fits in one view - no scrolling needed
                    content = full_content + "\n\n[Page content complete - no need to scroll]"

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
            # Closing context will save HAR file if recording was enabled
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
        """
        Create a new isolated browser session.

        Returns:
            BrowserSession instance
        """
        if self._playwright is None:
            await self.start()

        # Prepare context options
        context_options = {
            "viewport": {"width": 1280, "height": 720},
            "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "ignore_https_errors": False,
            "java_script_enabled": True,
            "bypass_csp": False,
        }

        if self._isolation_mode == "strict":
            browser = await self._playwright.chromium.launch(
                headless=self._headless,
                args=self._browser_args,
            )
            context = await browser.new_context(**context_options)
            context.set_default_timeout(PAGE_TIMEOUT_MS)
            page = await context.new_page()
            return BrowserSession(context, page, browser=browser)
        else:
            if self._browser is None:
                await self.start()

            context = await self._browser.new_context(**context_options)
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

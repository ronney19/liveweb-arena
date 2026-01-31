"""Browser engine with session isolation for concurrent evaluations"""

import asyncio
from typing import Optional, TYPE_CHECKING
from playwright.async_api import async_playwright, Browser, BrowserContext, Page, Playwright

from .models import BrowserObservation, BrowserAction

if TYPE_CHECKING:
    from .interceptor import CacheInterceptor

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
        self._cache_interceptor: Optional["CacheInterceptor"] = None

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

        # If cache interceptor is set, it handles routing
        if self._cache_interceptor:
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

        Uses regex-based route interception to properly handle special characters.
        Playwright's glob pattern treats ? as single-char wildcard, but we need
        literal ? for URLs like *?format=*.

        Args:
            patterns: List of URL patterns (glob-style with * wildcard)
                     Example: ["*api.example.com*", "*?format=*"]
        """
        import re
        self._blocked_patterns.extend(patterns)

        # Build a combined regex for all patterns (more efficient than multiple routes)
        regex_patterns = []
        for pattern in patterns:
            # Convert glob to regex: escape special chars, then convert \* back to .*
            regex_pattern = re.escape(pattern).replace(r'\*', '.*')
            regex_patterns.append(regex_pattern)

        if regex_patterns:
            combined_regex = re.compile('|'.join(regex_patterns), re.IGNORECASE)

            async def block_handler(route):
                url = route.request.url
                if combined_regex.search(url):
                    await route.abort("blockedbyclient")
                else:
                    await route.continue_()

            # Use **/* to intercept all requests, filter by regex
            await self._context.route("**/*", block_handler)

    async def set_cache_interceptor(self, interceptor: "CacheInterceptor"):
        """
        Set up cache-based request interception.

        Routes all requests through the interceptor for cache handling.

        Args:
            interceptor: CacheInterceptor instance
        """
        from liveweb_arena.core.interceptor import CacheInterceptor
        self._cache_interceptor = interceptor

        # Route all requests through the interceptor
        await self._context.route("**/*", interceptor.handle_route)

    def get_interceptor_stats(self) -> Optional[dict]:
        """Get cache interception statistics."""
        if hasattr(self, '_cache_interceptor') and self._cache_interceptor:
            return self._cache_interceptor.get_stats()
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
                clicked = False

                # First try the provided selector
                try:
                    await self._page.click(selector, timeout=timeout_ms)
                    clicked = True
                except Exception as click_err:
                    # If selector contains case-sensitive attribute match, try case-insensitive
                    if '[href*=' in selector or '[src*=' in selector:
                        import re
                        # Extract attribute and value: a[href*='GOOGL.US'] -> (href, GOOGL.US)
                        match = re.search(r"\[(\w+)\*=['\"]([^'\"]+)['\"]\]", selector, re.IGNORECASE)
                        if match:
                            attr_name = match.group(1).lower()
                            attr_value = match.group(2).lower()
                            # Use JavaScript to find element with case-insensitive match
                            element_handle = await self._page.evaluate_handle(f"""
                                () => {{
                                    const elements = document.querySelectorAll('a, button, [onclick]');
                                    for (const el of elements) {{
                                        const attr = el.getAttribute('{attr_name}');
                                        if (attr && attr.toLowerCase().includes('{attr_value}')) {{
                                            return el;
                                        }}
                                    }}
                                    return null;
                                }}
                            """)
                            if element_handle:
                                try:
                                    await element_handle.as_element().click()
                                    clicked = True
                                except Exception:
                                    pass

                    # If still not clicked, re-raise the original error
                    if not clicked:
                        raise click_err

                # Wait briefly for potential navigation
                await asyncio.sleep(0.3)

            elif action_type == "type":
                selector = params.get("selector", "")
                text = params.get("text", "")
                press_enter = params.get("press_enter", False)

                # Try provided selector first
                element = await self._page.query_selector(selector)

                # If selector doesn't match, try common fallbacks
                if not element:
                    fallback_selectors = [
                        'input[type="text"]:visible',
                        'input[type="search"]:visible',
                        'input:not([type="hidden"]):not([type="submit"]):visible',
                        'textarea:visible',
                        'input[name="s"]',  # Stooq search
                        'input[name="q"]',  # Common search name
                        '#search',
                        '[role="searchbox"]',
                    ]
                    for fallback in fallback_selectors:
                        try:
                            element = await self._page.query_selector(fallback)
                            if element:
                                selector = fallback
                                break
                        except Exception:
                            continue

                if element:
                    # Click first to trigger any onfocus/onclick handlers that set up form state
                    try:
                        await element.click()
                        await asyncio.sleep(0.1)
                    except Exception:
                        pass

                    # Fix form association for inputs not properly linked to their form
                    # (needed for cached pages where JS form setup hasn't run)
                    try:
                        await self._page.evaluate("""
                            (selector) => {
                                const input = document.querySelector(selector);
                                if (input && !input.form) {
                                    // Try to find and associate with nearest form
                                    const forms = document.forms;
                                    for (let i = 0; i < forms.length; i++) {
                                        const form = forms[i];
                                        if (form.id) {
                                            input.setAttribute('form', form.id);
                                            // Also set global form reference for Stooq-style JS
                                            if (typeof window.cmp_f === 'string' || !window.cmp_f) {
                                                window.cmp_f = form;
                                            }
                                            break;
                                        }
                                    }
                                }
                            }
                        """, selector)
                    except Exception:
                        pass

                    await self._page.fill(selector, text)
                    if press_enter:
                        await self._page.press(selector, "Enter")
                        # Wait briefly for potential navigation after Enter
                        await asyncio.sleep(0.3)
                else:
                    raise Exception(f"No element found for selector '{selector}'")

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
                count = await locator.count()

                # If no match with exact=True, try with exact=False
                if count == 0 and exact:
                    locator = self._page.get_by_role(role, name=name, exact=False)
                    count = await locator.count()

                # If still no match, try partial name match
                if count == 0 and name:
                    for keyword in name.split()[:3]:
                        if len(keyword) > 2:
                            partial_locator = self._page.get_by_role(role, name=keyword, exact=False)
                            partial_count = await partial_locator.count()
                            if partial_count > 0:
                                locator = partial_locator.first
                                count = 1
                                break

                if count > 0:
                    await locator.click(timeout=5000)
                    # Wait briefly for potential navigation
                    await asyncio.sleep(0.3)
                else:
                    raise Exception(f"No element found with role='{role}' name='{name}'")

            elif action_type == "type_role":
                role = params.get("role", "textbox")
                name = params.get("name", "")
                text = params.get("text", "")
                press_enter = params.get("press_enter", False)

                # Try exact name match first
                locator = self._page.get_by_role(role, name=name)
                count = await locator.count()

                # If no match with given name, try fallbacks for textbox
                if count == 0 and role == "textbox":
                    # Fallback 1: Try common search input selectors
                    search_selectors = [
                        'input[name="s"]',   # Stooq search
                        'input[name="q"]',   # Common search name
                        'input[type="search"]',
                        '#search',
                        '[role="searchbox"]',
                    ]
                    for selector in search_selectors:
                        try:
                            el = await self._page.query_selector(selector)
                            if el:
                                locator = self._page.locator(selector)
                                count = 1
                                break
                        except Exception:
                            continue

                    # Fallback 2: Try partial name match
                    if count == 0 and name:
                        for keyword in name.split()[:3]:
                            if len(keyword) > 2:
                                partial_locator = self._page.get_by_role(role, name=keyword)
                                partial_count = await partial_locator.count()
                                if partial_count > 0:
                                    locator = partial_locator.first
                                    count = 1
                                    break

                    # Fallback 3: Use first visible textbox
                    if count == 0:
                        empty_locator = self._page.get_by_role(role, name="")
                        empty_count = await empty_locator.count()
                        if empty_count > 0:
                            locator = empty_locator.first
                            count = 1

                if count > 0:
                    # Click first to trigger any onfocus/onclick handlers
                    try:
                        await locator.click()
                        await asyncio.sleep(0.1)
                    except Exception:
                        pass

                    # Fix form association for inputs not properly linked to their form
                    # Also fix window.cmp_f for Stooq-style sites where JS expects form reference
                    try:
                        await self._page.evaluate("""
                            () => {
                                const inputs = document.querySelectorAll('input[type="text"], input[type="search"]');
                                inputs.forEach(input => {
                                    if (!input.form) {
                                        const forms = document.forms;
                                        for (let i = 0; i < forms.length; i++) {
                                            const form = forms[i];
                                            if (form.id) {
                                                input.setAttribute('form', form.id);
                                                // Also set global form reference for Stooq-style JS
                                                if (typeof window.cmp_f === 'string' || !window.cmp_f) {
                                                    window.cmp_f = form;
                                                }
                                                break;
                                            }
                                        }
                                    }
                                });
                            }
                        """)
                    except Exception:
                        pass

                    await locator.fill(text)
                    if press_enter:
                        original_url = self._page.url
                        await locator.press("Enter")
                        # Wait briefly for potential navigation
                        await asyncio.sleep(0.5)

                        # If still on same page, try calling the JS redirect directly
                        # (handles cached pages where form handlers fail)
                        if self._page.url == original_url and text:
                            try:
                                # Try calling Stooq's cmp_u function directly
                                await self._page.evaluate(f"""
                                    () => {{
                                        if (typeof cmp_u === 'function') {{
                                            cmp_u('{text}');
                                        }} else {{
                                            // Fallback: direct navigation for search-style inputs
                                            const url = window.location.origin;
                                            if (url.includes('stooq')) {{
                                                window.location.href = url + '/q/?s=' + encodeURIComponent('{text}');
                                            }}
                                        }}
                                    }}
                                """)
                                # Wait for URL to actually change (navigation is async)
                                for _ in range(10):
                                    await asyncio.sleep(0.3)
                                    if self._page.url != original_url:
                                        break
                            except Exception:
                                pass
                else:
                    raise Exception(f"No element found with role='{role}' name='{name}'")

            elif action_type == "stop":
                # Stop action - no browser operation needed
                pass

            else:
                # Unknown action type - treat as wait
                await asyncio.sleep(0.5)

        except Exception as e:
            # Re-raise action execution errors so agent_loop can report failure
            raise

        return await self._get_observation()

    async def get_observation(self, max_retries: int = 3) -> BrowserObservation:
        """Get current browser observation with retry logic for navigation timing"""
        return await self._get_observation(max_retries)

    async def _get_observation(self, max_retries: int = 5) -> BrowserObservation:
        """Get current browser observation with retry logic for page loading.

        Key improvements:
        1. Validates content is meaningful before returning to AI
        2. Retries if content is empty/too short (page still loading)
        3. Returns clear error messages for blocked/failed pages
        """
        MIN_VALID_CONTENT_LENGTH = 50  # Minimum chars for valid content

        for attempt in range(max_retries):
            try:
                url = self._page.url

                # Check for error pages - no need to wait
                if url.startswith("chrome-error://") or url.startswith("about:neterror"):
                    return BrowserObservation(
                        url=url,
                        title="Error",
                        accessibility_tree="[Page failed to load - network error. Try a different URL.]",
                    )

                # Check for blocked pages (request was aborted)
                if url == "about:blank" and attempt > 0:
                    # Likely blocked by pattern - check if we have a pending URL
                    return BrowserObservation(
                        url=url,
                        title="Blocked",
                        accessibility_tree="[Navigation was blocked. The URL may be restricted. Try using the main website instead of API endpoints.]",
                    )

                # Wait for page to be fully loaded with increased timeout
                page_loaded = False
                try:
                    await self._page.wait_for_load_state("networkidle", timeout=15000)
                    page_loaded = True
                except Exception:
                    # Network idle timeout - page might still be loading
                    # Try domcontentloaded as fallback
                    try:
                        await self._page.wait_for_load_state("domcontentloaded", timeout=5000)
                        page_loaded = True
                    except Exception:
                        pass

                # If page not loaded and we have retries left, wait and retry
                if not page_loaded and attempt < max_retries - 1:
                    await asyncio.sleep(1.5)
                    continue

                title = await self._page.title()

                # Check for cached accessibility tree first (deterministic in cache mode)
                cached_tree = self._cache_interceptor.get_accessibility_tree(url) if self._cache_interceptor else None
                if cached_tree:
                    full_content = cached_tree
                else:
                    # Get accessibility tree from live page
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

                # Content validation: if content is too short, page may still be loading
                content_length = len(full_content.strip())
                if content_length < MIN_VALID_CONTENT_LENGTH and attempt < max_retries - 1:
                    # Wait longer and retry - page content not yet available
                    await asyncio.sleep(2.0)
                    continue

                # If content is still empty after all retries, provide helpful message
                if content_length < MIN_VALID_CONTENT_LENGTH:
                    full_content = f"[Page appears empty or content is minimal ({content_length} chars). The page may be:\n" \
                                   f"- Still loading (try scrolling or waiting)\n" \
                                   f"- Blocked (try the main website instead of API endpoints)\n" \
                                   f"- Requiring JavaScript that failed to load\n" \
                                   f"Current URL: {url}]\n\n{full_content}"

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
                        # Add clear truncation notice
                        content += "\n\n[Page content truncated - use view_more action to see more content]"
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

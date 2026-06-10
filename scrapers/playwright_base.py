"""Shared Playwright scraper base: browser lifecycle, anti-detection,
optional persistent profile (for logged-in sessions), block detection.
"""

import random
import logging

from scrapers.base import BaseScraper
from utils.user_agents import get_random_user_agent

logger = logging.getLogger(__name__)

BLOCKED_TITLE_KEYWORDS = [
    "captcha", "robot", "blocked", "verify", "security",
    "access denied", "just a moment", "attention required",
]

# Pinned UA for persistent (logged-in) profiles — rotating the UA on a
# logged-in session is itself a bot signal.
PERSISTENT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


class PlaywrightScraper(BaseScraper):
    def __init__(self, config):
        super().__init__(config)
        self._playwright = None
        self._browser = None       # Browser (ephemeral mode) or None
        self._context = None       # BrowserContext (persistent mode) or None
        self._persistent = False

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def _init_playwright(self, user_data_dir: str = None):
        # ── Windows / asyncio-loop fix ────────────────────────────────────────
        # When a Playwright scraper runs inside a ThreadPoolExecutor on Windows
        # (Python 3.10+), the worker thread inherits a running asyncio event
        # loop from the main thread.  Playwright's sync API detects that loop
        # and refuses to start.  Fix: replace the event loop in this thread
        # with a fresh one.
        import asyncio
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.set_event_loop(asyncio.new_event_loop())
        except RuntimeError:
            asyncio.set_event_loop(asyncio.new_event_loop())

        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise RuntimeError(
                "playwright is not installed. Run:  pip install playwright"
            )

        try:
            self._playwright = sync_playwright().start()
        except Exception as e:
            if "Executable doesn't exist" in str(e) or "playwright install" in str(e).lower():
                raise RuntimeError(
                    "Playwright browsers not downloaded. Run:  playwright install chromium"
                ) from e
            raise

        launch_args = [
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-blink-features=AutomationControlled",
        ]

        try:
            if user_data_dir:
                from pathlib import Path
                Path(user_data_dir).mkdir(parents=True, exist_ok=True)
                self._context = self._playwright.chromium.launch_persistent_context(
                    user_data_dir,
                    headless=self.config.playwright_headless,
                    args=launch_args,
                    user_agent=PERSISTENT_UA,
                    viewport={"width": 1366, "height": 768},
                    locale="en-GB",
                )
                self._persistent = True
                logger.info(f"{self.__class__.__name__}: persistent browser profile loaded ({user_data_dir})")
            else:
                self._browser = self._playwright.chromium.launch(
                    headless=self.config.playwright_headless,
                    args=launch_args,
                )
                logger.info(f"{self.__class__.__name__}: Playwright browser launched")
        except Exception:
            self._playwright.stop()
            raise

    def _close_playwright(self):
        try:
            if self._context:
                self._context.close()
            if self._browser:
                self._browser.close()
            if self._playwright:
                self._playwright.stop()
        except Exception:
            pass
        finally:
            self._context = None
            self._browser = None
            self._playwright = None

    def _new_context(self):
        """Fresh browser context (ephemeral mode) or the persistent context."""
        if self._persistent:
            return self._context
        width = random.randint(1280, 1440)
        height = random.randint(700, 800)
        return self._browser.new_context(
            user_agent=get_random_user_agent(),
            viewport={"width": width, "height": height},
            locale="en-GB",
            timezone_id="Europe/London",
            extra_http_headers={"Accept-Language": "en-GB,en;q=0.9"},
        )

    # ── Anti-detection ────────────────────────────────────────────────────────

    def _setup_page(self, page):
        page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            Object.defineProperty(navigator, 'languages', {get: () => ['en-GB', 'en']});
            Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3]});
        """)
        # Block heavy resources to reduce fingerprint and speed up
        page.route("**/*.{png,jpg,jpeg,gif,svg,ico,woff,woff2,ttf,eot}", lambda r: r.abort())
        page.route("**/*.css", lambda r: r.abort())

    @staticmethod
    def _is_blocked(page) -> bool:
        try:
            title = page.title().lower()
            return any(x in title for x in BLOCKED_TITLE_KEYWORDS)
        except Exception:
            return False

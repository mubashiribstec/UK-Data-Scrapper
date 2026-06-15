"""Browser-automation AI providers: ChatGPT and Gemini web UIs.

Instead of API keys, these providers drive the regular chat websites in a
real Chromium browser using a login session you save once with:

    python main.py --login-ai

Sessions persist as Chromium profiles under output/.browser/ and are reused
headless on every later run.

All prompts are serviced by ONE background worker thread that owns the
browser — enrichment runs in a thread pool, and a persistent profile can
only be opened by a single browser at a time, so calls are serialised here.
"""

import atexit
import logging
import queue
import threading
import time
from pathlib import Path
from typing import Optional

from utils.debug_snapshot import save_debug_snapshot

logger = logging.getLogger(__name__)

PROVIDERS = {
    "chatgpt": {
        "label": "ChatGPT",
        "url": "https://chatgpt.com/",
        "composer": [
            "#prompt-textarea",
            "div#prompt-textarea",
            "div[contenteditable='true'][id='prompt-textarea']",
            "textarea#prompt-textarea",
            "textarea[data-testid='prompt-textarea']",
            "div.ProseMirror[contenteditable='true']",
            "form[data-type='unified-composer'] div[contenteditable='true']",
            "main div[contenteditable='true']",
        ],
        "send": [
            "button[data-testid='send-button']",
            "button[aria-label*='Send']",
        ],
        "message": [
            "[data-message-author-role='assistant'] .markdown",
            "[data-message-author-role='assistant']",
        ],
        "busy": [
            "button[data-testid='stop-button']",
        ],
        "login_wall": [
            "button[data-testid='login-button']",
            "a[href*='auth/login']",
        ],
    },
    "gemini_web": {
        "label": "Gemini",
        "url": "https://gemini.google.com/app",
        "composer": [
            "rich-textarea div.ql-editor",
            "div.ql-editor[contenteditable='true']",
            "div[contenteditable='true'][role='textbox']",
            "div.ql-editor[role='textbox']",
            "main div[contenteditable='true']",
        ],
        "send": [
            "button[aria-label='Send message']",
            "button[aria-label*='Send']",
            "button.send-button",
        ],
        "message": [
            "model-response message-content",
            "message-content",
            ".model-response-text",
        ],
        "busy": [
            "button[aria-label*='Stop']",
        ],
        "login_wall": [
            "a[href*='accounts.google.com/ServiceLogin']",
            "a[aria-label*='Sign in']",
        ],
    },
}

# Best-effort selectors for cookie-consent / "stay logged out" interstitials
# that can cover the composer on a fresh session. Each is tried in order;
# failures are swallowed since most won't match on any given page.
_DISMISS_SELECTORS = [
    "button:has-text('Accept all')",
    "button:has-text('Accept All')",
    "button:has-text('Accept')",
    "button:has-text('I agree')",
    "button:has-text('Stay logged out')",
    "button:has-text('Reject all')",
    "[aria-label='Close']",
    "button[aria-label='Dismiss']",
]

# How long the answer text must stay unchanged before we treat it as complete
_STABLE_SECONDS = 4.0

# A real desktop Chrome UA. Headless Chromium's DEFAULT UA contains
# "HeadlessChrome", an instant bot signal that triggers Cloudflare's "verify
# you are human" wall — so we always override it, headless or not.
_REAL_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

_STEALTH_JS = (
    "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
    "Object.defineProperty(navigator, 'languages', {get: () => ['en-GB', 'en']});"
    "Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});"
)


class _HumanCheckHeadless(Exception):
    """Raised when a 'verify you are human' wall blocks a headless browser-AI
    page — signals the worker to retry the request in a visible window so the
    user can solve it."""


_request_q: queue.Queue = queue.Queue()
_worker_lock = threading.Lock()
_worker: Optional[threading.Thread] = None
_SHUTDOWN = object()


def profile_dir_for(provider: str, config) -> str:
    if provider == "chatgpt":
        return getattr(config, "chatgpt_profile_dir", "./output/.browser/chatgpt")
    return getattr(config, "gemini_web_profile_dir", "./output/.browser/gemini")


def browser_ai_ready(profile_dir: str) -> bool:
    """A saved login session exists from a previous --login-ai run."""
    p = Path(profile_dir or "")
    return bool(profile_dir) and p.is_dir() and any(p.iterdir())


# ── Public entry point ───────────────────────────────────────────────────────

def ask_browser_ai(prompt: str, config, provider: str, timeout: int = 180) -> Optional[str]:
    """Send a prompt to ChatGPT/Gemini web UI via the worker thread."""
    if provider not in PROVIDERS:
        raise RuntimeError(f"unknown browser AI provider '{provider}'")
    if not browser_ai_ready(profile_dir_for(provider, config)):
        raise RuntimeError(f"{provider}: no saved login session — run: python main.py --login-ai")

    _ensure_worker()
    done = threading.Event()
    box: dict = {}
    _request_q.put((prompt, config, provider, timeout, done, box))
    # Extra headroom so a one-off human-verification solve (visible window)
    # doesn't trip the caller-side timeout mid-solve.
    if not done.wait(timeout + 150):
        raise RuntimeError(f"{provider}: browser AI call timed out")
    if box.get("error"):
        raise RuntimeError(box["error"])
    return box.get("result")


def _ensure_worker():
    global _worker
    with _worker_lock:
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_worker_loop, daemon=True, name="browser-ai")
            _worker.start()


def _shutdown_worker():
    if _worker and _worker.is_alive():
        _request_q.put(_SHUTDOWN)
        _worker.join(timeout=15)


atexit.register(_shutdown_worker)


# ── Worker thread: owns all Playwright objects ───────────────────────────────

def _worker_loop():
    # Same Windows/asyncio fix as the scrapers: this thread may inherit a
    # running event loop, which Playwright's sync API refuses to run under.
    import asyncio
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.set_event_loop(asyncio.new_event_loop())
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())

    playwright = None
    sessions: dict[str, tuple] = {}   # provider -> (context, page, headful)
    escalated: set = set()            # providers forced visible after a human-check

    def _close_session(provider):
        ctx_page = sessions.pop(provider, None)
        if ctx_page:
            try:
                ctx_page[0].close()
            except Exception:
                pass

    def _launch(provider, config, headful):
        nonlocal playwright
        if playwright is None:
            from playwright.sync_api import sync_playwright
            playwright = sync_playwright().start()
        ctx = playwright.chromium.launch_persistent_context(
            profile_dir_for(provider, config),
            headless=not headful,
            args=["--no-sandbox", "--disable-dev-shm-usage",
                  "--disable-blink-features=AutomationControlled"],
            user_agent=_REAL_UA,
            viewport={"width": 1366, "height": 768},
            locale="en-GB",
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.add_init_script(_STEALTH_JS)
        sessions[provider] = (ctx, page, headful)
        logger.info(
            f"Browser AI: {PROVIDERS[provider]['label']} session opened "
            f"({'visible' if headful else 'headless'})"
        )
        return page

    def _get_page(provider, config):
        if provider in sessions:
            return sessions[provider][1]
        headful = (
            not getattr(config, "playwright_headless", True)
            or bool(getattr(config, "browser_ai_headful", False))
            or provider in escalated
        )
        return _launch(provider, config, headful)

    while True:
        item = _request_q.get()
        if item is _SHUTDOWN:
            break
        prompt, config, provider, timeout, done, box = item
        page = None
        try:
            page = _get_page(provider, config)
            headful = sessions[provider][2]
            box["result"] = _ask_in_page(page, prompt, PROVIDERS[provider], timeout, headful=headful)
        except _HumanCheckHeadless:
            # ChatGPT/Gemini is behind a "verify you are human" wall that can't
            # be solved in an invisible browser. Reopen this provider VISIBLE so
            # the user can solve it, remember that for the rest of the run, retry.
            logger.warning(
                f"Browser AI: {PROVIDERS[provider]['label']} needs human verification — "
                "opening a VISIBLE browser window so you can solve it (don't close it)"
            )
            escalated.add(provider)
            _close_session(provider)
            page = None
            try:
                page = _launch(provider, config, headful=True)
                box["result"] = _ask_in_page(page, prompt, PROVIDERS[provider], timeout, headful=True)
            except Exception as e:
                box["error"] = f"{provider}: {e}"
                if page is not None:
                    save_debug_snapshot(page, f"browser_ai_{provider}_error")
                _close_session(provider)
        except Exception as e:
            box["error"] = f"{provider}: {e}"
            if page is not None:
                save_debug_snapshot(page, f"browser_ai_{provider}_error")
            # Tear the session down so the next call starts from a clean browser
            _close_session(provider)
        finally:
            done.set()

    for provider in list(sessions):
        _close_session(provider)
    if playwright:
        try:
            playwright.stop()
        except Exception:
            pass


def _first_visible(page, selectors, timeout_ms=0):
    for sel in selectors:
        try:
            el = page.query_selector(sel)
            if el and el.is_visible():
                return el
        except Exception:
            continue
    if timeout_ms:
        deadline = time.time() + timeout_ms / 1000
        while time.time() < deadline:
            el = _first_visible(page, selectors)
            if el:
                return el
            time.sleep(0.5)
    return None


def _dismiss_interstitials(page) -> None:
    for sel in _DISMISS_SELECTORS:
        try:
            el = page.query_selector(sel)
            if el and el.is_visible():
                el.click(timeout=2000)
                time.sleep(0.3)
        except Exception:
            continue


_HUMAN_CHECK_TITLE_MARKERS = (
    "just a moment", "attention required", "verify", "robot", "are you human",
)


def _looks_like_human_check(page) -> bool:
    """Detect a Cloudflare / 'verify you are human' interstitial on the page."""
    try:
        title = (page.title() or "").lower()
        if any(m in title for m in _HUMAN_CHECK_TITLE_MARKERS):
            return True
    except Exception:
        pass
    for sel in (
        "iframe[src*='challenges.cloudflare.com']",
        "iframe[title*='Cloudflare']",
        "#challenge-stage",
        "#cf-challenge-running",
        "[id*='turnstile']",
        "input[name='cf-turnstile-response']",
    ):
        try:
            if page.query_selector(sel):
                return True
        except Exception:
            continue
    try:
        body = (page.inner_text("body") or "").lower()
        if "verify you are human" in body or "checking your browser" in body:
            return True
    except Exception:
        pass
    return False


def _ask_in_page(page, prompt: str, spec: dict, timeout: int, headful: bool = False) -> str:
    # A fresh navigation per prompt = a fresh conversation, so answers can't
    # bleed into each other.
    page.goto(spec["url"], timeout=45000, wait_until="domcontentloaded")
    time.sleep(1.0)
    _dismiss_interstitials(page)

    # When visible, give the user time to clear any human-verification wall.
    if headful and _looks_like_human_check(page):
        logger.warning(
            "Browser AI: a 'verify you are human' challenge is on screen — "
            "please solve it in the browser window (waiting up to 90s)..."
        )
    composer_timeout = 90000 if headful else 20000

    composer = _first_visible(page, spec["composer"], timeout_ms=composer_timeout)
    if not composer:
        if _first_visible(page, spec["login_wall"]):
            raise RuntimeError("session expired — run: python main.py --login-ai")
        if _looks_like_human_check(page):
            save_debug_snapshot(page, "browser_ai_human_check")
            if not headful:
                raise _HumanCheckHeadless()
            raise RuntimeError(
                "human-verification challenge not solved in time — re-run and "
                "solve it in the visible window, or run: python main.py --login-ai"
            )
        save_debug_snapshot(page, "browser_ai_composer_not_found")
        raise RuntimeError("chat composer not found (page layout changed or blocked)")

    n_before = len(page.query_selector_all(spec["message"][0]))

    composer.click()
    composer.fill(prompt)
    time.sleep(0.5)

    send = _first_visible(page, spec["send"])
    if send:
        send.click()
    else:
        page.keyboard.press("Enter")

    return _wait_for_answer(page, spec, n_before, timeout)


def _wait_for_answer(page, spec: dict, n_before: int, timeout: int) -> str:
    deadline = time.time() + timeout
    last_text = ""
    stable_since = None

    while time.time() < deadline:
        time.sleep(1.0)

        text = ""
        for sel in spec["message"]:
            els = page.query_selector_all(sel)
            if len(els) > n_before or (sel != spec["message"][0] and els):
                try:
                    text = els[-1].inner_text().strip()
                except Exception:
                    text = ""
                if text:
                    break
        if not text:
            continue

        busy = _first_visible(page, spec["busy"]) is not None

        if text != last_text:
            last_text = text
            stable_since = time.time()
            continue

        if not busy and stable_since and time.time() - stable_since >= _STABLE_SECONDS:
            return last_text

    if last_text:
        logger.warning("Browser AI: timeout while streaming — returning partial answer")
        return last_text
    save_debug_snapshot(page, "browser_ai_no_answer")
    raise RuntimeError("no answer appeared before timeout")


# ── One-time interactive login ───────────────────────────────────────────────

def run_ai_login(config) -> bool:
    """Open ChatGPT and Gemini in a visible browser, one after the other,
    so the user can log in once. Sessions persist to disk and are reused
    headless by every later run."""
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
        print("playwright is not installed. Run:  pip install playwright && playwright install chromium")
        return False

    ok = False
    with sync_playwright() as p:
        for provider, spec in PROVIDERS.items():
            profile_dir = profile_dir_for(provider, config)
            label = spec["label"]

            answer = input(f"\nLog in to {label}? [Y/n/skip] > ").strip().lower()
            if answer in ("n", "no", "skip", "s"):
                print(f"Skipped {label}.")
                continue

            Path(profile_dir).mkdir(parents=True, exist_ok=True)
            # Match the UA/stealth used by headless runs so the human-verification
            # cookie (cf_clearance) saved here is still valid later.
            ctx = p.chromium.launch_persistent_context(
                profile_dir,
                headless=False,
                args=["--no-sandbox", "--disable-dev-shm-usage",
                      "--disable-blink-features=AutomationControlled"],
                user_agent=_REAL_UA,
                viewport={"width": 1366, "height": 768},
                locale="en-GB",
            )
            try:
                page = ctx.pages[0] if ctx.pages else ctx.new_page()
                page.add_init_script(_STEALTH_JS)
                page.goto(spec["url"], timeout=60000, wait_until="domcontentloaded")

                print("=" * 64)
                print(f"{label.upper()} LOGIN")
                print("=" * 64)
                print(f"A browser window has opened on {spec['url']}")
                print("1. Sign in with your account (complete any 2FA/OTP)")
                print("2. Solve any 'verify you are human' check if shown")
                print("3. Wait until you can see the normal chat screen")
                print("=" * 64)
                input(f"When you are logged in to {label}, press Enter here to save the session... ")
                ok = True
                print(f"{label} session saved to {profile_dir}\n")
            except Exception as e:
                logger.error(f"{label} login failed: {e}")
                print(f"{label} login failed: {e}")
            finally:
                try:
                    ctx.close()
                except Exception:
                    pass

    if ok:
        print("Done. Future runs with --ai will use the browser session(s) automatically.")
    return ok

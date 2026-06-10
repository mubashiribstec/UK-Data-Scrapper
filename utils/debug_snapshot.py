"""Shared helper: save a screenshot + HTML snapshot of a Playwright page to
output/debug/ for diagnosing selector/bot-detection failures."""

import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)


def save_debug_snapshot(page, name: str) -> None:
    """Best-effort: never raises."""
    try:
        debug_dir = Path("output/debug")
        debug_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        base = debug_dir / f"{name}_{ts}"
        page.screenshot(path=str(base.with_suffix(".png")), full_page=True)
        base.with_suffix(".html").write_text(page.content(), encoding="utf-8")
        logger.info(f"{name}: saved debug snapshot to {base}.png/.html")
    except Exception as e:
        logger.debug(f"{name}: failed to save debug snapshot: {e}")

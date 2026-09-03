"""Optional: Svenska kraftnät's operational information page, as plain text.

Used only by the explainer, and only extractively. Any failure is swallowed —
this must never block a run.
"""

from __future__ import annotations

import logging
import re

from ..config import RAW_DIR, SVK_DRIFTINFO_URL, SVK_TEXT_PATH
from .http import get

log = logging.getLogger(__name__)

MAX_CHARS = 8000


def fetch_svk_text() -> tuple[str | None, dict]:
    try:
        html = get(SVK_DRIFTINFO_URL, retries=2).text
    except Exception as exc:  # noqa: BLE001
        log.info("SVK driftinfo unavailable: %s", exc)
        return None, {"ok": False, "error": str(exc)[:200]}

    try:
        text = _extract_text(html)
    except Exception as exc:  # noqa: BLE001
        return None, {"ok": False, "error": f"parse failed: {exc}"[:200]}

    if not text:
        return None, {"ok": False, "error": "no readable text"}

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    SVK_TEXT_PATH.write_text(text, encoding="utf-8")
    return text, {"ok": True, "chars": len(text)}


def _extract_text(html: str) -> str:
    try:
        from bs4 import BeautifulSoup  # type: ignore

        soup = BeautifulSoup(html, "lxml")
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()
        blocks = [t.get_text(" ", strip=True) for t in soup.find_all(["p", "li", "h2", "h3"])]
    except Exception:  # noqa: BLE001 - fall back to a regex strip
        stripped = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.S | re.I)
        stripped = re.sub(r"<[^>]+>", " ", stripped)
        blocks = [re.sub(r"\s+", " ", stripped).strip()]

    text = "\n".join(b for b in blocks if len(b) > 30)
    return text[:MAX_CHARS]


def load_cached_svk_text() -> str | None:
    if not SVK_TEXT_PATH.exists():
        return None
    try:
        return SVK_TEXT_PATH.read_text(encoding="utf-8")
    except OSError:
        return None

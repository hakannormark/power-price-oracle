"""EUR/SEK reference rate from the ECB, needed to show prices in öre/kWh.

The market trades in EUR/MWh, so every öre figure on the site is a currency
conversion, not a unit conversion. The ECB publishes one reference rate per
working day; the last one is cached so a weekend or a failed fetch still gives
the UI something to convert with.
"""

from __future__ import annotations

import json
import logging
import xml.etree.ElementTree as ET

from ..config import ECB_FX_URL, FX_PATH, FX_SANITY_RANGE
from ..timeutil import iso, now_local
from .http import get

log = logging.getLogger(__name__)

CURRENCY = "SEK"


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def parse_rate(xml_text: str) -> tuple[float, str]:
    """Pull the SEK rate and its reference date out of the ECB daily file."""
    root = ET.fromstring(xml_text)
    date = ""
    for element in root.iter():
        if _local_name(element.tag) != "Cube":
            continue
        if "time" in element.attrib:
            date = element.attrib["time"]
        if element.attrib.get("currency") == CURRENCY:
            return float(element.attrib["rate"]), date
    raise ValueError(f"no {CURRENCY} rate in ECB response")


def load_cached() -> dict | None:
    if not FX_PATH.exists():
        return None
    try:
        return json.loads(FX_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def fetch_eur_sek() -> tuple[dict | None, dict]:
    """Current EUR/SEK, falling back to the last stored rate."""
    cached = load_cached()
    try:
        rate, date = parse_rate(get(ECB_FX_URL, retries=2).text)
    except Exception as exc:  # noqa: BLE001 - fall back rather than fail the run
        log.warning("ECB FX fetch failed: %s", exc)
        if cached:
            stale = dict(cached, stale=True)
            return stale, {"ok": False, "error": f"{exc}"[:160], "using_cached": cached["date"]}
        return None, {"ok": False, "error": f"{exc}"[:200]}

    low, high = FX_SANITY_RANGE
    if not low <= rate <= high:
        # A rate outside this band means the feed changed shape, not that the
        # krona moved. Better to keep yesterday's number than to publish nonsense.
        log.warning("EUR/SEK %.4f outside sanity range %s", rate, FX_SANITY_RANGE)
        if cached:
            return dict(cached, stale=True), {"ok": False, "error": f"implausible rate {rate}"}
        return None, {"ok": False, "error": f"implausible rate {rate}"}

    payload = {
        "pair": "EUR/SEK",
        "rate": round(rate, 4),
        "date": date,
        "source": "ECB",
        "fetched_at": iso(now_local()),
        "stale": False,
    }
    FX_PATH.parent.mkdir(parents=True, exist_ok=True)
    FX_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    log.info("EUR/SEK %.4f (ECB %s)", rate, date)
    return payload, {"ok": True, "rate": payload["rate"], "date": date}

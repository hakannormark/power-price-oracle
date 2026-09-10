"""Gas and carbon daily closes: the cost of the plant that sets the price in the south.

When the southern links are open, SE3 and SE4 import the continental price,
and on the continent the marginal plant most hours burns gas and pays for its
carbon. Both series are read for that one number (see longterm/data.py).

Source: Yahoo Finance's public chart endpoint. It needs no key but is
unofficial and has no service level, so a failed fetch degrades and the stored
history in data/market/fuels.jsonl carries the model through the gap.

    TTF=F   ICE Dutch TTF front-month gas future, EUR/MWh
    CO2.L   SparkChange physically backed EUA ETC, EUR per allowance. The
            allowance futures are not on the endpoint; a physically backed
            product tracks the allowance price closely.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from ..config import FUEL_SYMBOLS, FUELS_PATH, YAHOO_CHART_URL
from ..store import r3, read_jsonl, write_jsonl
from .http import get

log = logging.getLogger(__name__)

# The endpoint refuses clients that do not present a browser-like agent.
BROWSER_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; PowerPriceOracle/1.0)"}


def parse_chart(payload: dict) -> list[tuple[str, float]]:
    """(ISO date, close) pairs from a chart response, gaps dropped, one per day."""
    result = (payload.get("chart") or {}).get("result") or []
    if not result:
        return []
    body = result[0]
    stamps = body.get("timestamp") or []
    quotes = ((body.get("indicators") or {}).get("quote") or [{}])[0]
    closes = quotes.get("close") or []
    by_day: dict[str, float] = {}
    for stamp, close in zip(stamps, closes):
        if close is None:
            continue
        by_day[datetime.fromtimestamp(stamp, timezone.utc).date().isoformat()] = float(close)
    return sorted(by_day.items())


def load_fuels() -> list[dict]:
    return list(read_jsonl(FUELS_PATH))


def upsert_fuels(rows: list[dict]) -> int:
    """Merge closes keyed on (series, date); a later fetch of the same day wins."""
    merged = {(r["series"], r["date"]): r for r in load_fuels()}
    added = sum(1 for r in rows if (r["series"], r["date"]) not in merged)
    for row in rows:
        merged[(row["series"], row["date"])] = row
    write_jsonl(FUELS_PATH, sorted(merged.values(), key=lambda r: (r["series"], r["date"])))
    return added


def fetch_fuels(history: bool = False) -> tuple[list[dict], dict]:
    """Recent closes for every series, or ten years of them when `history` is set.

    Never raises: the model runs on stored history if the endpoint is down.
    """
    span = "10y" if history else "3mo"
    rows: list[dict] = []
    errors: list[str] = []
    for series, symbol in FUEL_SYMBOLS.items():
        try:
            payload = get(
                YAHOO_CHART_URL.format(symbol=symbol),
                params={"range": span, "interval": "1d"},
                retries=2,
                headers=BROWSER_HEADERS,
            ).json()
            points = parse_chart(payload)
            if not points:
                errors.append(f"{series}: empty")
            rows.extend({"series": series, "symbol": symbol, "date": day, "close": r3(close)} for day, close in points)
        except Exception as exc:  # noqa: BLE001 - degrade per series
            errors.append(f"{series}: {type(exc).__name__}")
    status: dict = {"ok": bool(rows) and not errors, "rows": len(rows)}
    if errors:
        status["error"] = "; ".join(errors)[:200]
    return rows, status


def refresh() -> dict:
    """Fetch and store; a first run pulls the full history."""
    rows, status = fetch_fuels(history=not FUELS_PATH.exists())
    if rows:
        status["added"] = upsert_fuels(rows)
    return status


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    rows, status = fetch_fuels(history=True)
    if rows:
        status["added"] = upsert_fuels(rows)
    print(status)

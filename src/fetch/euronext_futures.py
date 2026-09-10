"""Nordic power futures from Euronext Nord Pool: the market's own price for the months ahead.

Since March 2026 the Nordic system price and area-difference (EPAD) futures
trade on Euronext. Its public quotes page shows the latest settlement for the
front month, quarter and year of every product. The page renders the table
with a POST to a Drupal endpoint, carrying a block definition that the page
itself embeds; this adapter does the same two steps.

An area price is the system price plus that area's EPAD for the same
delivery period. EPAD codes map to the Swedish zones by city:

    LUL Luleå SE1   SUN Sundsvall SE2   STO Stockholm SE3   MAL Malmö SE4

Settlements are end-of-day, delayed data. Only settlements are stored, one
snapshot per trading day, in data/market/futures.jsonl. There is no free
settlement archive, so the history starts on the day this adapter first ran.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date, timedelta
from html import unescape

import pandas as pd

from ..config import FUTURES_PATH
from ..store import r3, read_jsonl, write_jsonl
from ..timeutil import now_local
from .http import get, post

log = logging.getLogger(__name__)

PAGE_URL = "https://live.euronext.com/en/products/commodities/power-derivatives/contracts-list"
BLOCK_URL = "https://live.euronext.com/en/ajax/featured_derivatives_contracts/get_block_content"
BROWSER_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; PowerPriceOracle/1.0)"}

SYSTEM = "SYS"
EPAD_ZONES = {"LUL": "SE1", "SUN": "SE2", "STO": "SE3", "MAL": "SE4"}
TENORS = ("month", "quarter", "year")  # most specific first
MONTHS = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}

# Column positions in each product table: tenor, code, delivery, last, +/-,
# on vol, off vol, tot vol, open, high, low, settl, OI, aggr vol, aggr OI.
COL_TENOR, COL_CODE, COL_DELIVERY, COL_SETTLEMENT = 0, 1, 2, 11


# ------------------------------------------------------------------ parsing


def _text(cell: str) -> str:
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", cell))).strip()


def _number(text: str) -> float | None:
    cleaned = text.replace(",", "").strip()
    try:
        return float(cleaned)
    except ValueError:
        return None


def product_for(caption: str) -> str | None:
    """'SYS' for the system price, 'SE1'..'SE4' for Swedish EPADs, else None."""
    if "System Price" in caption and "EPAD" not in caption:
        return SYSTEM
    match = re.search(r"Sweden (LUL|SUN|STO|MAL)\b", caption)
    return EPAD_ZONES[match.group(1)] if match else None


def delivery_period(tenor: str, label: str) -> tuple[str, str] | None:
    """(first day, first day after) of a day, week, month, quarter or year delivery."""
    tenor = tenor.lower()
    if tenor == "day":
        match = re.fullmatch(r"(\d{1,2}) ([A-Z][a-z]{2}) (\d{4})", label)
        if not match or match.group(2) not in MONTHS:
            return None
        start = date(int(match.group(3)), MONTHS[match.group(2)], int(match.group(1)))
        return start.isoformat(), (start + timedelta(days=1)).isoformat()
    if tenor == "week":
        match = re.fullmatch(r"Week (\d{1,2}) (\d{4})", label)
        if not match:
            return None
        start = date.fromisocalendar(int(match.group(2)), int(match.group(1)), 1)
        return start.isoformat(), (start + timedelta(days=7)).isoformat()
    if tenor == "month":
        match = re.fullmatch(r"([A-Z][a-z]{2}) (\d{4})", label)
        if not match or match.group(1) not in MONTHS:
            return None
        start = pd.Period(year=int(match.group(2)), month=MONTHS[match.group(1)], freq="M")
        return start.start_time.date().isoformat(), (start + 1).start_time.date().isoformat()
    if tenor == "quarter":
        match = re.fullmatch(r"Q([1-4]) (\d{4})", label)
        if not match:
            return None
        start = pd.Period(year=int(match.group(2)), quarter=int(match.group(1)), freq="Q")
        return start.start_time.date().isoformat(), (start + 1).start_time.date().isoformat()
    if tenor == "year":
        match = re.fullmatch(r"(\d{4})", label)
        if not match:
            return None
        year = int(match.group(1))
        return date(year, 1, 1).isoformat(), date(year + 1, 1, 1).isoformat()
    return None


def parse_block(html: str, trade_date: str) -> list[dict]:
    """Settlement rows for the system price and the Swedish EPADs."""
    rows: list[dict] = []
    for caption, table in re.findall(r"<caption>(.*?)</caption>\s*<table.*?>(.*?)</table>", html, re.S):
        product = product_for(_text(caption))
        if product is None:
            continue
        for body in re.findall(r"<tr>(.*?)</tr>", table, re.S):
            cells = [_text(c) for c in re.findall(r"<td[^>]*>(.*?)</td>", body, re.S)]
            if len(cells) <= COL_SETTLEMENT:
                continue
            tenor = cells[COL_TENOR].lower()
            period = delivery_period(tenor, cells[COL_DELIVERY])
            settlement = _number(cells[COL_SETTLEMENT])
            if period is None or settlement is None:
                continue
            rows.append(
                {
                    "trade_date": trade_date,
                    "product": product,
                    "tenor": tenor,
                    "code": cells[COL_CODE],
                    "delivery": cells[COL_DELIVERY],
                    "delivery_start": period[0],
                    "delivery_end": period[1],
                    "settlement": r3(settlement),
                }
            )
    return rows


def _form_fields(prefix: str, value, out: list[tuple[str, str]]) -> None:
    """jQuery-style form encoding of a nested block definition."""
    if isinstance(value, dict):
        for key, inner in value.items():
            _form_fields(f"{prefix}[{key}]", inner, out)
    elif isinstance(value, list):
        for index, inner in enumerate(value):
            _form_fields(f"{prefix}[{index}]", inner, out)
    else:
        out.append((prefix, "" if value is None else str(value)))


def block_definitions(page_html: str) -> tuple[str, dict]:
    """Language prefix and block definitions embedded in the quotes page."""
    match = re.search(
        r'<script[^>]*data-drupal-selector="drupal-settings-json"[^>]*>(.*?)</script>', page_html, re.S
    )
    if not match:
        raise ValueError("drupal settings not found on the quotes page")
    settings = json.loads(match.group(1))
    return settings.get("path", {}).get("pathPrefix", "en/"), settings.get("custom", {}).get("blocks", {})


# ------------------------------------------------------------------ fetch and store


def fetch_futures() -> tuple[list[dict], dict]:
    """Today's settlement snapshot. Never raises."""
    trade_date = now_local().date().isoformat()
    try:
        page = get(PAGE_URL, retries=2, headers=BROWSER_HEADERS).text
        prefix, blocks = block_definitions(page)
        rows: list[dict] = []
        for definition in blocks.values():
            fields: list[tuple[str, str]] = [("lang", prefix)]
            _form_fields("content_data", definition, fields)
            html = post(
                BLOCK_URL,
                data=fields,
                retries=2,
                headers={**BROWSER_HEADERS, "X-Requested-With": "XMLHttpRequest"},
            ).text
            rows.extend(parse_block(html, trade_date))
    except Exception as exc:  # noqa: BLE001 - the forecast runs without futures
        log.warning("Euronext futures unavailable: %s", exc)
        return [], {"ok": False, "rows": 0, "error": f"{type(exc).__name__}: {exc}"[:200]}
    products = {r["product"] for r in rows}
    missing = sorted({SYSTEM, *EPAD_ZONES.values()} - products)
    status: dict = {"ok": bool(rows) and not missing, "rows": len(rows)}
    if missing:
        status["error"] = "missing products: " + ", ".join(missing)
    return rows, status


def load_futures() -> list[dict]:
    return list(read_jsonl(FUTURES_PATH))


def upsert_futures(rows: list[dict]) -> int:
    """Keyed on (trade_date, code, delivery): a later fetch the same day wins."""
    key = lambda r: (r["trade_date"], r["code"], r["delivery"])  # noqa: E731
    merged = {key(r): r for r in load_futures()}
    added = sum(1 for r in rows if key(r) not in merged)
    for row in rows:
        merged[key(row)] = row
    write_jsonl(FUTURES_PATH, sorted(merged.values(), key=lambda r: (r["trade_date"], r["product"], r["delivery_start"])))
    return added


# ------------------------------------------------------------------ area prices


def latest_snapshot(rows: list[dict], as_of: str | None = None) -> list[dict]:
    """The most recent trading day's rows, on or before `as_of` (ISO date)."""
    days = sorted({r["trade_date"] for r in rows if as_of is None or r["trade_date"] <= as_of})
    return [r for r in rows if days and r["trade_date"] == days[-1]]


def implied_month_price(snapshot: list[dict], zone: str, month: pd.Period) -> dict | None:
    """System plus EPAD for `month`, from the most specific tenor quoted for both.

    A quarter or year contract is a flat price over its whole delivery, so a
    month taken from one carries no month-specific shape; `tenor` says which.
    """
    first = month.start_time.date().isoformat()
    for tenor in TENORS:
        def covering(product: str) -> dict | None:
            for row in snapshot:
                if (
                    row["product"] == product
                    and row["tenor"] == tenor
                    and row["delivery_start"] <= first < row["delivery_end"]
                ):
                    return row
            return None

        system, epad = covering(SYSTEM), covering(zone)
        if system and epad:
            return {
                "price": r3(system["settlement"] + epad["settlement"]),
                "tenor": tenor,
                "system": system["settlement"],
                "epad": epad["settlement"],
                "contracts": [system["code"], epad["code"]],
                "delivery": system["delivery"],
                "trade_date": system["trade_date"],
            }
    return None


SYSTEM_DAY_TENORS = ("week", "month", "quarter")
EPAD_DAY_TENORS = ("month", "quarter", "year")


def zone_price_for_day(snapshot: list[dict], zone: str, day: date) -> dict | None:
    """The market's price for one delivery day in a zone, or None.

    The system part comes from the most specific contract covering the day:
    week, then month, then quarter. The zone's EPAD is traded only per month
    and the current month drops out once it is in delivery, so when no EPAD
    covers the day the next quoted one stands in for it (`epad_proxy`).
    """
    first = day.isoformat()

    def covering(product: str, tenors: tuple[str, ...]) -> dict | None:
        for tenor in tenors:
            for row in snapshot:
                if (
                    row["product"] == product
                    and row["tenor"] == tenor
                    and row["delivery_start"] <= first < row["delivery_end"]
                ):
                    return row
        return None

    system = covering(SYSTEM, SYSTEM_DAY_TENORS)
    if system is None:
        return None
    epad, proxy = covering(zone, EPAD_DAY_TENORS), False
    if epad is None:
        later = sorted(
            (r for r in snapshot if r["product"] == zone and r["tenor"] in EPAD_DAY_TENORS and r["delivery_start"] > first),
            key=lambda r: (r["delivery_start"], EPAD_DAY_TENORS.index(r["tenor"])),
        )
        epad, proxy = (later[0] if later else None), True
    if epad is None:
        return None
    return {
        "price": r3(system["settlement"] + epad["settlement"]),
        "period": (system["code"], system["delivery"], epad["code"], epad["delivery"]),
        "system_tenor": system["tenor"],
        "epad_proxy": proxy,
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    fetched, state = fetch_futures()
    if fetched:
        state["added"] = upsert_futures(fetched)
    print(state)
    for row in fetched:
        print(row["product"], row["tenor"], row["delivery"], row["settlement"])

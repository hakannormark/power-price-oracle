"""Market aggregates per bidding zone for the BESS screening map.

Everything here is zone-level. No market series exists per municipality, station
or plant, so nothing below may be presented as the revenue of a named site.

Sources
-------
* Day-ahead spot: the repository's own actuals (ENTSO-E / Nord Pool), hourly.
* mFRR and aFRR capacity markets: Svenska kraftnät Data Service (CKAN,
  data.svk.se), marginal price EUR/MW and contracted volume per zone and hour.
* FCR: national (SE + DK2 common market). Taken from the curated yearly table in
  site/data/bess-utility.json and shown as context only: it does not separate
  the zones, so it carries no weight in the geographic score.

The SvK download is cached in the published file and refreshed at most once per
calendar day, so the hourly pipeline does not hammer data.svk.se.
"""

from __future__ import annotations

import json
import logging
import math
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from ..config import SITE_DATA_DIR
from ..timeutil import TZ, iso, now_local

log = logging.getLogger(__name__)

ZONES = ("SE1", "SE2", "SE3", "SE4")
MAP_DIR = SITE_DATA_DIR / "bess-map"
MARKET_PATH = MAP_DIR / "market.json"
UTILITY_PATH = SITE_DATA_DIR / "bess-utility.json"

SVK_API = "https://data.svk.se/sv/api/3/action/datastore_search"
SVK_RESOURCES = {
    "mfrr_cm": "0c56e30d-8fce-4c27-afc8-621c230ae34d",  # mfrr_capacity_market
    "afrr_cm": "6351d2cc-1657-43eb-b112-b8408c700529",  # afrr_capacity_market
}
SVK_PAGE = 32000

# Round-trip efficiency used in the arbitrage proxy. A screening constant, shown in UI.
RTE = 0.88

SCORE_VERSION = "2026.10-1"


# --------------------------------------------------------------------- helpers
def _quantile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    s = sorted(values)
    pos = (len(s) - 1) * q
    lo, hi = math.floor(pos), math.ceil(pos)
    if lo == hi:
        return s[lo]
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


def _r(value: float | None, nd: int = 2) -> float | None:
    return None if value is None else round(float(value), nd)


def _parse_ts(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


# ------------------------------------------------------------------------ spot
def _daily_arbitrage(prices: list[float], hours: int) -> float:
    """EUR per MW and day for one full cycle of a battery with `hours` of storage.

    Sells the `hours` most expensive hours and buys the `hours` cheapest, losing
    (1 - RTE) on the way. Order inside the day is ignored: across consecutive
    days the cycles chain, and the error is small next to the forecast error a
    real desk carries. Perfect foresight, so it is an upper bound per zone.
    """
    if len(prices) < 2 * hours:
        return 0.0
    s = sorted(prices)
    buy = sum(s[:hours]) / RTE
    sell = sum(s[-hours:])
    return max(0.0, sell - buy)


def _spot_block(rows: Iterable[tuple[datetime, float]]) -> dict[str, Any] | None:
    by_day: dict[str, list[float]] = defaultdict(list)
    all_prices: list[float] = []
    for ts, price in rows:
        by_day[ts.astimezone(TZ).date().isoformat()].append(price)
        all_prices.append(price)
    days = {d: p for d, p in by_day.items() if len(p) >= 20}
    if not days:
        return None
    n = len(all_prices)
    mean = sum(all_prices) / n
    std = math.sqrt(sum((p - mean) ** 2 for p in all_prices) / n)
    spreads = [max(p) - min(p) for p in days.values()]
    scale = 365.0 / len(days)  # annualise partial years
    arb2 = sum(_daily_arbitrage(p, 2) for p in days.values())
    arb4 = sum(_daily_arbitrage(p, 4) for p in days.values())
    return {
        "hours": n,
        "days": len(days),
        "mean": _r(mean),
        "std": _r(std),
        "p10": _r(_quantile(all_prices, 0.10)),
        "p90": _r(_quantile(all_prices, 0.90)),
        "neg_share": _r(sum(1 for p in all_prices if p < 0) / n, 4),
        "daily_spread_mean": _r(sum(spreads) / len(spreads)),
        "daily_spread_p90": _r(_quantile(spreads, 0.90)),
        "arb_2h_eur_mw_yr": _r(arb2 * scale, 0),
        "arb_4h_eur_mw_yr": _r(arb4 * scale, 0),
    }


def spot_aggregates(actuals: list[dict], now: datetime) -> dict[str, Any]:
    """Last-12-months block and one block per calendar year, per zone."""
    cutoff = now - timedelta(days=365)
    series: dict[str, list[tuple[datetime, float]]] = defaultdict(list)
    for row in actuals:
        zone = row.get("zone")
        price = row.get("price_eur_mwh")
        if zone not in ZONES or price is None:
            continue
        ts = _parse_ts(row["ts"])
        if ts > now:  # tomorrow's auction result is not history yet
            continue
        series[zone].append((ts, float(price)))

    out: dict[str, Any] = {}
    for zone in ZONES:
        rows = sorted(series.get(zone, []))
        recent = [(t, p) for t, p in rows if t >= cutoff]
        by_year: dict[int, list[tuple[datetime, float]]] = defaultdict(list)
        for t, p in rows:
            by_year[t.astimezone(TZ).year].append((t, p))
        years = {}
        for year in sorted(by_year):
            block = _spot_block(by_year[year])
            if block and block["days"] >= 60:
                block["partial"] = block["days"] < 330
                years[str(year)] = block
        out[zone] = {
            "last12m": _spot_block(recent),
            "years": years,
            "first_ts": iso(rows[0][0]) if rows else None,
            "last_ts": iso(rows[-1][0]) if rows else None,
        }

    # Congestion: hourly price differences between neighbouring zones.
    def _diff(a: str, b: str) -> dict[str, Any] | None:
        pa = {t: p for t, p in series.get(a, []) if t >= cutoff}
        diffs = [pa[t] - p for t, p in series.get(b, []) if t in pa]
        if not diffs:
            return None
        return {
            "mean": _r(sum(diffs) / len(diffs)),
            "p90": _r(_quantile(diffs, 0.90)),
            "share_hours_gt_1": _r(sum(1 for d in diffs if d > 1) / len(diffs), 3),
        }

    out["_congestion"] = {"SE4-SE3": _diff("SE4", "SE3"), "SE3-SE2": _diff("SE3", "SE2"), "SE2-SE1": _diff("SE2", "SE1")}
    return out


# ------------------------------------------------------------- SvK capacity
def _get_json(url: str, timeout: int = 60) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "power-price-oracle/1.0 (bess-map)"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - fixed https host
        return json.loads(resp.read().decode("utf-8"))


def fetch_svk_capacity(resource_id: str, since_utc: datetime, max_pages: int = 12) -> list[dict]:
    """Records newer than `since_utc`, newest first, paged on _id."""
    records: list[dict] = []
    for page in range(max_pages):
        query = urllib.parse.urlencode(
            {"resource_id": resource_id, "limit": SVK_PAGE, "offset": page * SVK_PAGE, "sort": "_id desc"}
        )
        payload = _get_json(f"{SVK_API}?{query}")
        batch = payload.get("result", {}).get("records", [])
        if not batch:
            break
        newest_in_batch = None
        for rec in batch:
            try:
                ts = datetime.fromisoformat(rec["start_time_utc"]).replace(tzinfo=timezone.utc)
            except (KeyError, TypeError, ValueError):
                continue
            newest_in_batch = ts if newest_in_batch is None or ts > newest_in_batch else newest_in_batch
            if ts >= since_utc:
                rec["_ts"] = ts
                records.append(rec)
        if newest_in_batch is not None and newest_in_batch < since_utc:
            break
        if len(batch) < SVK_PAGE:
            break
    return records


def capacity_aggregates(records: list[dict]) -> dict[str, Any]:
    """Per zone and direction: hourly-averaged price and volume statistics.

    Records are averaged within the hour first, so a switch from hourly to
    15-minute products cannot double-count the value of an hour.
    """
    hourly: dict[tuple[str, str], dict[datetime, list[tuple[float, float]]]] = defaultdict(lambda: defaultdict(list))
    for rec in records:
        zone, direction = rec.get("bidding_zone"), rec.get("reserve_direction")
        price, volume = rec.get("price"), rec.get("volume")
        if zone not in ZONES or direction not in ("up", "down") or price is None:
            continue
        hour = rec["_ts"].replace(minute=0, second=0, microsecond=0)
        hourly[(zone, direction)][hour].append((float(price), float(volume or 0.0)))

    out: dict[str, Any] = {}
    for (zone, direction), hours in hourly.items():
        prices, volumes, paid = [], [], 0.0
        for vals in hours.values():
            p = sum(v[0] for v in vals) / len(vals)
            q = sum(v[1] for v in vals) / len(vals)
            prices.append(p)
            volumes.append(q)
            paid += sum(v[0] * v[1] for v in vals) / len(vals)
        n = len(prices)
        # The window is a fixed 365 days. An hour without a record is an hour in
        # which nothing was procured in that zone, so it counts as zero; scaling
        # up to 8760 would invent revenue in thin markets.
        scale = 1.0
        out.setdefault(zone, {})[direction] = {
            "hours": n,
            "coverage": _r(n / 8760.0, 3),
            "price_mean": _r(sum(prices) / n),
            "price_p90": _r(_quantile(prices, 0.90)),
            "share_hours_gt_10": _r(sum(1 for p in prices if p > 10) / n, 3),
            "volume_mean_mw": _r(sum(volumes) / n, 1),
            # EUR a 1 MW bid would have earned if accepted every hour at the marginal price.
            "value_eur_mw_yr": _r(sum(prices) * scale, 0),
            # What the market paid all accepted bids in the window (not one plant's revenue).
            "paid_eur_yr": _r(paid * scale, 0),
        }
    return out


# ------------------------------------------------------------------- FCR (national)
def fcr_context() -> dict[str, Any]:
    try:
        util = json.loads(UTILITY_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    hist = util.get("historical_prices", {})
    req = util.get("requirements", {})
    out = {}
    for key in ("fcr_n", "fcr_d_up", "fcr_d_down"):
        series = hist.get(key) or {}
        if not series:
            continue
        year = max(series)
        out[key] = {
            "year": year,
            "price_eur_mw_h": series[year],
            "history": series,
            "demand_mw": (req.get(key) or {}).get("2026"),
        }
    return out


# ------------------------------------------------------------------ assembly
def _previous() -> dict[str, Any]:
    try:
        return json.loads(MARKET_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def build_market(actuals: list[dict], fx_rate: float, now: datetime | None = None, fetch: bool = True) -> dict[str, Any]:
    now = now or now_local()
    previous = _previous()
    spot = spot_aggregates(actuals, now)

    capacity = previous.get("capacity") or {}
    cap_sources = previous.get("capacity_sources") or {}
    today = now.date().isoformat()
    stale = any((cap_sources.get(k) or {}).get("retrieved_at", "")[:10] != today for k in SVK_RESOURCES)
    if fetch and (stale or not capacity):
        since = (now - timedelta(days=365)).astimezone(timezone.utc)
        new_capacity, new_sources = {}, {}
        for key, rid in SVK_RESOURCES.items():
            try:
                recs = fetch_svk_capacity(rid, since)
                agg = capacity_aggregates(recs)
                if not agg:
                    raise ValueError("no records")
                new_capacity[key] = agg
                newest = max(r["_ts"] for r in recs)
                oldest = min(r["_ts"] for r in recs)
                new_sources[key] = {
                    "source": f"Svenska kraftnät Data Service, resource {rid}",
                    "url": "https://data.svk.se/dataset/" + key.replace("_cm", "_capacity_market"),
                    "retrieved_at": iso(now),
                    "valid_from": iso(oldest.astimezone(TZ)),
                    "valid_to": iso(newest.astimezone(TZ)),
                    "records": len(recs),
                }
            except Exception as exc:  # noqa: BLE001 - keep yesterday's numbers rather than none
                log.warning("SvK %s fetch failed, keeping cached aggregate: %s", key, exc)
                if key in capacity:
                    new_capacity[key] = capacity[key]
                    new_sources[key] = cap_sources.get(key, {})
        capacity, cap_sources = new_capacity, new_sources

    first_ts = min((spot[z]["first_ts"] for z in ZONES if spot[z]["first_ts"]), default=None)
    last_ts = max((spot[z]["last_ts"] for z in ZONES if spot[z]["last_ts"]), default=None)

    return {
        "version": SCORE_VERSION,
        "generated_at": iso(now),
        "fx_eur_sek": fx_rate,
        "rte": RTE,
        "spot": spot,
        "spot_source": {
            "source": "ENTSO-E Transparency / Nord Pool day-ahead, lagrat i data/actuals",
            "retrieved_at": iso(now),
            "valid_from": first_ts,
            "valid_to": last_ts,
        },
        "capacity": capacity,
        "capacity_sources": cap_sources,
        "fcr": fcr_context(),
        "fcr_source": {
            "source": "Svenska kraftnät Mimer / årsmedel, kurerad tabell i bess-utility.json",
            "note": "Gemensam marknad SE + DK2. Särskiljer inte elområden.",
        },
        "disclaimer": (
            "Indikativ screening från öppna källor. Visar inte ledig effekt i en station och ersätter inte "
            "förfrågan till nätägare. Marknadsintäkt är elområdets historiska pris, inte anläggningens intäkt. "
            "Träffgrad och stackning är antaganden."
        ),
    }


def write_market(payload: dict[str, Any], path: Path = MARKET_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def main() -> None:  # pragma: no cover - manual refresh
    from ..store import load_actuals
    from ..config import FX_PATH

    logging.basicConfig(level=logging.INFO)
    try:
        fx = float(json.loads(FX_PATH.read_text()).get("rate", 11.3))
    except Exception:  # noqa: BLE001
        fx = 11.3
    payload = build_market(load_actuals(), fx)
    write_market(payload)
    print(json.dumps({z: payload["spot"][z]["last12m"] for z in ZONES}, indent=1))
    print(json.dumps(payload["capacity"], indent=1)[:3000])


if __name__ == "__main__":  # pragma: no cover
    main()

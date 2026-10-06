"""Hourly market series from Svenska kraftnät for the BESS dispatch back-test.

Three public sources, none of which needs a token:

* Data Service (CKAN, data.svk.se): mFRR and aFRR capacity markets, marginal
  price EUR/MW and procured volume MW per zone, direction and hour; and the
  day-ahead area prices, used only to fill the hole in data/actuals between
  2020-10 and 2021-12.
* Mimer (mimer.svk.se): FCR-N, FCR-D up and FCR-D down, volume-weighted price
  EUR/MW and total procured volume per hour. One common market for SE + DK2,
  so the series is national.

Raw downloads are cached under data/raw/svk/ (git-ignored) and refreshed when
older than `max_age_h`. Everything is returned keyed on the UTC hour.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ..config import DATA_DIR, RAW_DIR
from ..timeutil import TZ

log = logging.getLogger(__name__)

CACHE_DIR = RAW_DIR / "svk"
SVK_API = "https://data.svk.se/sv/api/3/action/datastore_search"
MIMER_FCR = "https://mimer.svk.se/PrimaryRegulation/DownloadText"
RESOURCES = {
    "mfrr": "0c56e30d-8fce-4c27-afc8-621c230ae34d",
    "afrr": "6351d2cc-1657-43eb-b112-b8408c700529",
    "dayahead": "cf392ef3-7345-4e98-970e-83088c185570",
}
PAGE = 32000
UA = {"User-Agent": "power-price-oracle/1.0 (bess-dispatch)"}
ZONES = ("SE1", "SE2", "SE3", "SE4")

# Day-ahead prices for the months data/actuals does not hold. Tracked, so the
# back-test is reproducible without the download.
SPOT_FILL_PATH = DATA_DIR / "bess" / "spot_svk_2020_2021.json"
SPOT_FILL_FROM = datetime(2020, 9, 30, 22, tzinfo=timezone.utc)  # 2020-10-01 local
SPOT_FILL_TO = datetime(2021, 12, 31, 23, tzinfo=timezone.utc)   # exclusive, 2022-01-01 local


def _get(url: str, timeout: int = 120) -> bytes:
    last: Exception | None = None
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - fixed https hosts
                return resp.read()
        except Exception as exc:  # noqa: BLE001 - public servers, retry
            last = exc
            time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"download failed: {url[:120]}: {last}")


def _fresh(path: Path, max_age_h: float) -> bool:
    return path.exists() and (time.time() - path.stat().st_mtime) < max_age_h * 3600


def _utc(value: str) -> datetime:
    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)


# ----------------------------------------------------------------- CKAN
def _ckan_all(resource: str, name: str, max_age_h: float, stop_after: datetime | None = None) -> list[dict]:
    """Every record of a resource, oldest first. `stop_after` ends the paging early."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / f"{name}.json"
    if _fresh(path, max_age_h):
        return json.loads(path.read_text(encoding="utf-8"))
    keep = ("start_time_utc", "bidding_zone", "reserve_direction", "price", "volume")
    out: list[dict] = []
    offset = 0
    while True:
        query = urllib.parse.urlencode({
            "resource_id": resource, "limit": PAGE, "offset": offset, "sort": "start_time_utc asc,_id asc",
        })
        batch = json.loads(_get(f"{SVK_API}?{query}"))["result"]["records"]
        if not batch:
            break
        out.extend({k: r.get(k) for k in keep if k in r} for r in batch)
        log.info("svk %s: %s records (to %s)", name, len(out), batch[-1].get("start_time_utc"))
        if stop_after is not None and _utc(batch[-1]["start_time_utc"]) > stop_after:
            break
        if len(batch) < PAGE:
            break
        offset += PAGE
    path.write_text(json.dumps(out, separators=(",", ":")), encoding="utf-8")
    return out


def capacity_series(kind: str, max_age_h: float = 20) -> dict[tuple[str, str], dict[datetime, tuple[float, float]]]:
    """{(zone, direction): {utc_hour: (price EUR/MW, volume MW)}} for 'mfrr' or 'afrr'.

    Records are averaged within the hour, so 15-minute products do not count
    an hour four times. An hour with no record is an hour with no procurement.
    """
    recs = _ckan_all(RESOURCES[kind], kind, max_age_h)
    acc: dict[tuple[str, str], dict[datetime, list[tuple[float, float]]]] = defaultdict(lambda: defaultdict(list))
    for r in recs:
        zone, direction, price = r.get("bidding_zone"), r.get("reserve_direction"), r.get("price")
        if zone not in ZONES or direction not in ("up", "down") or price is None:
            continue
        hour = _utc(r["start_time_utc"]).replace(minute=0, second=0, microsecond=0)
        acc[(zone, direction)][hour].append((float(price), float(r.get("volume") or 0.0)))
    return {
        key: {h: (sum(v[0] for v in vals) / len(vals), sum(v[1] for v in vals) / len(vals)) for h, vals in hours.items()}
        for key, hours in acc.items()
    }


# ---------------------------------------------------------------- Mimer
def _mimer_month(first: date, max_age_h: float) -> str:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    nxt = (first.replace(day=28) + timedelta(days=4)).replace(day=1)
    path = CACHE_DIR / f"fcr_{first:%Y_%m}.csv"
    closed = nxt <= date.today() - timedelta(days=3)  # a finished month never changes
    if path.exists() and (closed or _fresh(path, max_age_h)):
        return path.read_text(encoding="utf-8")
    query = urllib.parse.urlencode({
        "periodFrom": f"{first:%m/%d/%Y} 00:00:00", "periodTo": f"{nxt:%m/%d/%Y} 00:00:00",
        "auctionTypeId": 1, "productTypeId": 0,
    })
    text = _get(f"{MIMER_FCR}?{query}").decode("utf-8-sig", errors="replace")
    path.write_text(text, encoding="utf-8")
    return text


def _num(cell: str) -> float | None:
    cell = cell.strip().replace("\xa0", "").replace(" ", "").replace(",", ".")
    try:
        return float(cell)
    except ValueError:
        return None


def fcr_series(start: date, end: date, max_age_h: float = 20) -> dict[str, dict[datetime, tuple[float, float]]]:
    """{'fcr_n' | 'fcr_d_up' | 'fcr_d_down': {utc_hour: (price EUR/MW, total volume MW)}}."""
    out: dict[str, dict[datetime, tuple[float, float]]] = {"fcr_n": {}, "fcr_d_up": {}, "fcr_d_down": {}}
    cols = {"fcr_n": (1, 2), "fcr_d_up": (8, 9), "fcr_d_down": (15, 16)}
    month = start.replace(day=1)
    while month <= end:
        text = _mimer_month(month, max_age_h)
        seen: dict[datetime, int] = defaultdict(int)
        for line in text.splitlines()[1:]:
            parts = line.split(";")
            if len(parts) < 17:
                continue
            try:
                naive = datetime.strptime(parts[0].strip(), "%Y-%m-%d %H:%M:%S")
            except ValueError:
                continue
            # Local wall-clock time. The repeated hour at the end of summer time
            # appears twice; the second occurrence is the later (fold=1) one.
            fold = seen[naive]
            seen[naive] += 1
            utc_hour = naive.replace(tzinfo=TZ, fold=min(fold, 1)).astimezone(timezone.utc)
            for key, (pc, vc) in cols.items():
                price, vol = _num(parts[pc]), _num(parts[vc])
                if price is not None:
                    out[key][utc_hour] = (price, vol or 0.0)
        month = (month.replace(day=28) + timedelta(days=4)).replace(day=1)
    return out


# ------------------------------------------------------ spot 2020-10..2021-12
def spot_fill(refresh: bool = False) -> dict[str, dict[datetime, float]]:
    """{zone: {utc_hour: EUR/MWh}} for the months missing from data/actuals."""
    if SPOT_FILL_PATH.exists() and not refresh:
        raw = json.loads(SPOT_FILL_PATH.read_text(encoding="utf-8"))
        start = datetime.fromisoformat(raw["start_utc"])
        return {
            z: {start + timedelta(hours=i): p for i, p in enumerate(vals) if p is not None}
            for z, vals in raw["prices"].items()
        }
    recs = _ckan_all(RESOURCES["dayahead"], "dayahead_2020_2021", 24 * 365, stop_after=SPOT_FILL_TO)
    acc: dict[str, dict[datetime, list[float]]] = {z: defaultdict(list) for z in ZONES}
    for r in recs:
        zone, price = r.get("bidding_zone"), r.get("price")
        if zone not in ZONES or price is None:
            continue
        ts = _utc(r["start_time_utc"])
        if SPOT_FILL_FROM <= ts < SPOT_FILL_TO:
            acc[zone][ts.replace(minute=0, second=0, microsecond=0)].append(float(price))
    out = {z: {h: sum(v) / len(v) for h, v in hours.items()} for z, hours in acc.items()}
    n_hours = int((SPOT_FILL_TO - SPOT_FILL_FROM).total_seconds() // 3600)
    payload: dict[str, Any] = {
        "source": "Svenska kraftnät Data Service, market_data_day_ahead_area_prices",
        "url": "https://data.svk.se/dataset/market_data_day_ahead_area_prices",
        "retrieved_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "start_utc": SPOT_FILL_FROM.isoformat(),
        "unit": "EUR/MWh, hourly, index = hours since start_utc",
        "prices": {
            z: [(round(out[z][SPOT_FILL_FROM + timedelta(hours=i)], 2)
                 if (SPOT_FILL_FROM + timedelta(hours=i)) in out[z] else None) for i in range(n_hours)]
            for z in ZONES
        },
    }
    SPOT_FILL_PATH.parent.mkdir(parents=True, exist_ok=True)
    SPOT_FILL_PATH.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    return out

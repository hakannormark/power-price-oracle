"""Day-ahead prices from energy-charts.info (Fraunhofer ISE), for filling gaps in the history.

    python -m src.fetch.energy_charts 2020-10-01 2021-12-31

No key is needed and the data is published under CC BY 4.0. The pipeline does
not use this in daily operation; it is here because the price history had a
hole from October 2020 to December 2021, between the end of the OPSD file and
the start of what was fetched from elprisetjustnu.se.
"""

from __future__ import annotations

import sys
from datetime import date, datetime, timedelta, timezone

from ..config import ZONES
from ..timeutil import TZ, iso, now_local
from .http import get

URL = "https://api.energy-charts.info/price"


def fetch_prices(zone: str, start: date, end: date) -> list[dict]:
    """Hourly day-ahead prices in EUR/MWh for start..end inclusive, as rows for the store."""
    payload = get(URL, params={"bzn": zone, "start": start.isoformat(), "end": end.isoformat()}).json()
    stamped = iso(now_local())
    by_hour: dict[datetime, list[float]] = {}
    for seconds, price in zip(payload.get("unix_seconds") or [], payload.get("price") or []):
        if price is None:
            continue
        ts = datetime.fromtimestamp(seconds, tz=timezone.utc)
        by_hour.setdefault(ts.replace(minute=0, second=0, microsecond=0), []).append(float(price))
    return [
        {"ts": iso(ts.astimezone(TZ)), "zone": zone, "price_eur_mwh": round(sum(v) / len(v), 2),
         "resolution": "PT60M", "published_at": stamped, "via": "energy-charts"}
        for ts, v in sorted(by_hour.items())
    ]


def main(argv: list[str]) -> int:  # pragma: no cover - network
    from ..store import upsert_actuals

    start, end = date.fromisoformat(argv[1]), date.fromisoformat(argv[2])
    total = 0
    for zone in ZONES:
        day = start
        while day <= end:
            last = min(end, day + timedelta(days=89))
            total += upsert_actuals(fetch_prices(zone, day, last))
            day = last + timedelta(days=1)
    print(f"stored {total} rows")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main(sys.argv))

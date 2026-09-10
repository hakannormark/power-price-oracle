"""market_scaled: the default model's hourly shape, moved to the futures market's level.

Every other model takes its level from prices that have already happened —
last week, the last four weeks, yesterday. A shift that is known but not yet in
those prices is invisible to all of them: four reactors going out, a cable
restriction starting next week. The 8 September 2026 miss was exactly that.

The futures market prices next week before it happens. This model keeps
shrunk_scaled's hourly shape and shifts each futures delivery period so that
its mean equals the market's price for the zone:

    zone price = system-price contract (week, else month, else quarter)
               + the zone's EPAD (the month's, else the next quoted one)

Two approximations are stated rather than hidden. EPADs trade per month and
the current month drops out once it is in delivery, so for the rest of a month
the next quoted month's differential stands in. And the seven-day window
covers only part of a week contract, so the shift is fitted on the covered
hours, whose mean differs from the full week's by the weekday/weekend shape.
Periods with fewer than MIN_COVERED_HOURS are left unshifted.

There is no free settlement history to back-test against (Nasdaq removed its
archive, Euronext keeps five trading days, EEX charges), so this model is
measured live from September 2026 and is not the site default.
"""

from __future__ import annotations

from datetime import datetime
from typing import Callable

from ..fetch.euronext_futures import latest_snapshot, load_futures, zone_price_for_day
from ..timeutil import to_local
from .base import ForecastPoint, order_quantiles

BASE_MODEL = "shrunk_scaled"
MIN_COVERED_HOURS = 72


class MarketScaled:
    id = "market_scaled"
    name_sv = "Dämpad nivå, marknadsjusterad"
    description_sv = (
        "Standardmodellens dygnsform, flyttad så att snittet över varje terminsperiod blir "
        "terminsmarknadens pris för elområdet: systemprisets veckokontrakt plus områdets "
        "prisdifferens (EPAD). Terminspriset väger in sådant marknaden redan känner till — "
        "kärnkraftsstopp, magasin, gaspris — som förra veckans pris inte gör. Det finns ingen "
        "fri historik att backtesta mot, så den mäts skarpt sedan september 2026 och styr "
        "inte sajten förrän den har visat sig bättre."
    )
    quantiles = True
    derived = True

    def __init__(self, futures_loader: Callable[[], list[dict]] = load_futures):
        self._load_futures = futures_loader

    def combine(self, predictions: dict[str, list[ForecastPoint]], issued_at: datetime) -> list[ForecastPoint]:
        """Shift the base model per (zone, futures period). Nothing to shift from: no output."""
        base = predictions.get(BASE_MODEL) or []
        snapshot = latest_snapshot(self._load_futures(), to_local(issued_at).date().isoformat())
        if not base or not snapshot:
            return []

        levels: dict[tuple, dict | None] = {}
        groups: dict[tuple, list[int]] = {}
        for index, point in enumerate(base):
            day = to_local(point.ts).date()
            if (point.zone, day) not in levels:
                levels[(point.zone, day)] = zone_price_for_day(snapshot, point.zone, day)
            level = levels[(point.zone, day)]
            if level is not None:
                groups.setdefault((point.zone, level["period"], level["price"]), []).append(index)

        shifts = [0.0] * len(base)
        for (_, _, price), members in groups.items():
            if len(members) < MIN_COVERED_HOURS:
                continue
            delta = price - sum(base[i].p50 for i in members) / len(members)
            for i in members:
                shifts[i] = delta

        out: list[ForecastPoint] = []
        for point, delta in zip(base, shifts):
            p10, p50, p90 = order_quantiles(point.p10 + delta, point.p50 + delta, point.p90 + delta)
            out.append(ForecastPoint(ts=point.ts, zone=point.zone, p10=p10, p50=p50, p90=p90))
        return out

    def predict(self, features, issued_at: datetime) -> list[ForecastPoint]:  # pragma: no cover
        raise NotImplementedError("market_scaled is derived: the pipeline calls combine().")

"""How reserve prices have moved with the spot price, measured, for the extreme year.

    python -m src.geo.reserve_comovement     # measure and write into dispatch_backtest.json

BESS Studio's positive extreme year re-optimises the battery against 2022's
spot prices. The reserve prices were left at the base period's, on the grounds
that 2022's own were set by a market the batteries had not yet filled. That is
half the picture: on days when the spot price is high, the upward reserves are
dearer in today's market too, because the hydro that provides them gives up
more by holding back.

Measured here, on every day since January 2024: each day's mean price per
product and its zone's mean spot price, both divided by the median of their
calendar month, which removes the steady fall in reserve prices. The elasticity
is how much dearer the product was on the tenth of days with the highest spot
price, against how much higher the spot price was:

    elasticity = ln(product, top tenth / rest) / ln(spot, top tenth / rest)

The factor for the extreme year is then (mean spot 2022 / mean spot in the base
period) ** elasticity, per zone and product, kept within 0.5-3.

It is a relation between days, applied to a whole year. That is an assumption,
and the page says so; the user can choose unchanged reserve prices instead.
"""

from __future__ import annotations

import json
import math
import sys
from collections import defaultdict
from datetime import date, datetime, timezone
from statistics import median
from typing import Any

from ..config import SITE_DATA_DIR

OUT_PATH = SITE_DATA_DIR / "bess-map" / "dispatch_backtest.json"
SINCE = date(2024, 1, 1)
EXTREME_YEAR = 2022
WEAK_YEAR = 2020
ZONES = ("SE1", "SE2", "SE3", "SE4")
FCR_SPOT_ZONE = "SE3"      # the FCR price is one for the country
FACTOR_RANGE = (0.5, 3.0)
ELASTICITY_RANGE = (-0.5, 1.5)


def _daily(series: dict[datetime, float]) -> dict[date, float]:
    days: dict[date, list[float]] = defaultdict(list)
    for ts, value in series.items():
        if value is not None and not math.isnan(value):
            days[ts.astimezone(timezone.utc).date()].append(value)
    return {d: sum(v) / len(v) for d, v in days.items() if len(v) >= 20}


def _relative(daily: dict[date, float]) -> dict[date, float]:
    """Each day against the median of its calendar month."""
    months: dict[tuple[int, int], list[float]] = defaultdict(list)
    for d, v in daily.items():
        months[(d.year, d.month)].append(v)
    out = {}
    for d, v in daily.items():
        mid = median(months[(d.year, d.month)])
        if mid > 0:
            out[d] = v / mid
    return out


def elasticity(pairs: list[tuple[float, float]]) -> tuple[float, float, float] | None:
    """(elasticity, spot ratio, product ratio) from (relative spot, relative price) per day."""
    if len(pairs) < 200:
        return None
    pairs = sorted(pairs)
    cut = int(len(pairs) * 0.9)
    rest, top = pairs[:cut], pairs[cut:]
    mean = lambda rows, i: sum(r[i] for r in rows) / len(rows)  # noqa: E731
    spot_ratio = mean(top, 0) / mean(rest, 0)
    price_ratio = mean(top, 1) / mean(rest, 1)
    if spot_ratio <= 1.05 or price_ratio <= 0:
        return None
    e = math.log(price_ratio) / math.log(spot_ratio)
    return max(ELASTICITY_RANGE[0], min(ELASTICITY_RANGE[1], e)), spot_ratio, price_ratio


def measure(actuals: list[dict], periods: dict[str, dict], until: date | None = None) -> dict[str, Any]:
    from . import svk_data

    until = until or datetime.now(timezone.utc).date()
    spot: dict[str, dict[datetime, float]] = {z: {} for z in ZONES}
    for r in actuals:
        if r["zone"] in spot and (r["ts"][:4] in (str(EXTREME_YEAR), str(WEAK_YEAR)) or r["ts"][:4] >= str(SINCE.year)):
            spot[r["zone"]][datetime.fromisoformat(r["ts"]).astimezone(timezone.utc)] = r["price_eur_mwh"]
    rel_spot = {z: _relative(_daily({t: v for t, v in s.items() if t.date() >= SINCE})) for z, s in spot.items()}

    pairs: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for product, series in svk_data.fcr_series(SINCE, until, max_age_h=1e9).items():
        rel = _relative(_daily({t: v[0] for t, v in series.items()}))
        pairs[product] += [(rel_spot[FCR_SPOT_ZONE][d], p) for d, p in rel.items() if d in rel_spot[FCR_SPOT_ZONE]]
    for kind in ("mfrr", "afrr"):
        for (zone, direction), series in svk_data.capacity_series(kind, max_age_h=1e9).items():
            if zone not in rel_spot:
                continue
            rel = _relative(_daily({t: v[0] for t, v in series.items() if t.date() >= SINCE}))
            pairs[f"{kind}_{direction}"] += [(rel_spot[zone][d], p) for d, p in rel.items() if d in rel_spot[zone]]

    products = {}
    for product, rows in sorted(pairs.items()):
        found = elasticity(rows)
        if found:
            products[product] = {"elasticity": round(found[0], 3), "days": len(rows),
                                 "spot_top_tenth": round(found[1], 2), "price_top_tenth": round(found[2], 2)}

    def year_mean(zone: str, year: int) -> float:
        values = [v for t, v in spot[zone].items() if t.year == year]
        return sum(values) / len(values) if values else 0.0

    def scaled(year: int) -> tuple[dict, dict]:
        """Per base period and zone: that year's spot against the period's, and each product's factor."""
        factors: dict[str, dict[str, dict[str, float]]] = {}
        ratios: dict[str, dict[str, float]] = {}
        for name, span in periods.items():
            start, end = datetime.fromisoformat(span["from"]), datetime.fromisoformat(span["to"])
            factors[name], ratios[name] = {}, {}
            for zone in ZONES:
                values = [v for t, v in spot[zone].items() if start <= t < end]
                mean = year_mean(zone, year)
                if not values or mean <= 0:
                    continue
                ratio = max(0.2, mean / (sum(values) / len(values)))
                ratios[name][zone] = round(ratio, 3)
                factors[name][zone] = {
                    p: round(max(FACTOR_RANGE[0], min(FACTOR_RANGE[1], ratio ** info["elasticity"])), 3)
                    for p, info in products.items()
                }
        return ratios, factors

    spot_ratio, factors = scaled(EXTREME_YEAR)
    spot_ratio_weak, factors_weak = scaled(WEAK_YEAR)
    return {
        "method": "Dagsmedel per produkt mot elområdets dagsmedel för spot, båda delade med kalendermånadens median, "
                  f"sedan {SINCE.isoformat()}. Elasticitet = ln(produkt, dyraste tiondelen dagar / övriga) / ln(spot, samma dagar). "
                  "Faktor = (medelspot 2022 / medelspot i basperioden) ^ elasticitet, inom 0,5–3.",
        "measured_through": until.isoformat(),
        "products": products,
        "spot_ratio_2022": spot_ratio,
        "factors": factors,
        "spot_ratio_2020": spot_ratio_weak,
        "factors_weak": factors_weak,
    }


def main() -> int:  # pragma: no cover - needs the cached SvK downloads
    from ..store import load_actuals

    payload = json.loads(OUT_PATH.read_text(encoding="utf-8"))
    payload["reserve_comovement"] = measure(load_actuals(), payload["periods"])
    OUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(json.dumps(payload["reserve_comovement"], ensure_ascii=False, indent=1)[:2600])
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

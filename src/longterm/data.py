"""Monthly building blocks, each cut off at the moment a forecast is issued.

Every function takes an issue instant and returns only what was knowable then:
prices before it, a reservoir reading old enough to have been published,
outage messages published before it, fuel closes before it. The backtest is
exactly as honest as these functions are, which is why they live apart from
the models.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pandas as pd

from ..config import ZONES
from ..fetch.nordpool_umm import dedupe_events, known_at
from ..timeutil import TZ, parse_iso, to_local

# A gas plant at about 50 % efficiency burns 2 MWh of gas and emits about
# 0.37 t CO2 per MWh of power. Its running cost, 2 × TTF + 0.37 × EUA, is the
# continental price SE3 and SE4 import whenever their southern links are open.
GAS_HEAT_RATE = 2.0
GAS_EMISSIONS_T = 0.37

# ENTSO-E publishes a week's reservoir filling some days after the week ends.
# A reading is only treated as known a week after its timestamp.
RESERVOIR_PUBLICATION_LAG = timedelta(days=7)

RECENT_DAYS = 30


# ------------------------------------------------------------------ prices


def daily_prices(actuals: list[dict]) -> pd.DataFrame:
    """Daily mean price per zone, indexed by local date. Thin days are dropped."""
    frame = pd.DataFrame(
        [
            {"zone": r["zone"], "ts": r["ts"], "price": float(r["price_eur_mwh"])}
            for r in actuals
            if r.get("zone") in ZONES and r.get("price_eur_mwh") is not None
        ]
    )
    if frame.empty:
        return pd.DataFrame(columns=list(ZONES))
    stamps = pd.to_datetime(frame["ts"], format="ISO8601", utc=True).dt.tz_convert("Europe/Stockholm")
    frame["day"] = stamps.dt.date
    grouped = frame.groupby(["day", "zone"])["price"].agg(["mean", "count"]).reset_index()
    grouped = grouped[grouped["count"] >= 20]
    return grouped.pivot(index="day", columns="zone", values="mean").sort_index()


def monthly_prices(daily: pd.DataFrame) -> pd.DataFrame:
    """Calendar-month mean per zone; a month missing more than a day is NaN."""
    if daily.empty:
        return pd.DataFrame(columns=list(ZONES))
    months = pd.PeriodIndex(pd.to_datetime(pd.Index(daily.index)), freq="M")
    means = daily.groupby(months).mean()
    counts = daily.groupby(months).count()
    days = pd.Series([p.days_in_month for p in means.index], index=means.index)
    return means.where(counts.ge(days - 1, axis=0))


def month_start(period: pd.Period) -> datetime:
    return datetime(period.year, period.month, 1, tzinfo=TZ)


def month_end(period: pd.Period) -> datetime:
    return month_start(period + 1)


def target_months(issue: datetime, horizons: tuple[int, ...]) -> list[pd.Period]:
    """Horizon 1 is the next calendar month after the issue date."""
    base = pd.Period(to_local(issue).date(), freq="M")
    return [base + h for h in horizons]


def recent_mean(daily: pd.DataFrame, issue: datetime, days: int = RECENT_DAYS) -> dict[str, float | None]:
    """Mean of the last `days` full days before the issue date."""
    day = to_local(issue).date()
    window = daily[(daily.index >= day - timedelta(days=days)) & (daily.index < day)]
    out: dict[str, float | None] = {}
    for zone in ZONES:
        values = window[zone].dropna() if zone in window else pd.Series(dtype=float)
        out[zone] = float(values.mean()) if len(values) >= days * 0.7 else None
    return out


def climatology(monthly: pd.DataFrame, issue: datetime) -> pd.DataFrame:
    """Median price per calendar month over months that ended before the issue.

    Median rather than mean: the record starts in the autumn of 2022, and one
    crisis quarter would otherwise set the seasonal shape for years.
    """
    done = [p for p in monthly.index if month_end(p) <= issue]
    usable = monthly.loc[done]
    if usable.empty:
        return pd.DataFrame(columns=list(ZONES))
    return usable.groupby(usable.index.month).median()


def seasonal_diff(clim: pd.DataFrame, issue: datetime, target: pd.Period, zone: str) -> float | None:
    """How much the target month usually differs from the month just behind us."""
    recent_month = (to_local(issue) - timedelta(days=RECENT_DAYS // 2)).month
    if target.month not in clim.index or recent_month not in clim.index or zone not in clim:
        return None
    ahead, behind = clim.at[target.month, zone], clim.at[recent_month, zone]
    if pd.isna(ahead) or pd.isna(behind):
        return None
    return float(ahead - behind)


# ------------------------------------------------------------------ hydro


def national_reservoirs(reservoirs: list[dict]) -> pd.Series:
    """Swedish stored hydro energy, MWh, summed over the four zones per reading.

    One national figure rather than four: the zones share one river system
    and one market, and SE4's own storage is too small to mean anything.
    """
    if not reservoirs:
        return pd.Series(dtype=float)
    frame = pd.DataFrame(reservoirs)
    frame["ts"] = pd.to_datetime(frame["ts"], format="ISO8601", utc=True)
    wide = frame.pivot_table(index="ts", columns="zone", values="stored_mwh", aggfunc="last")
    wide = wide.dropna(subset=[z for z in ZONES if z in wide.columns])
    return wide.sum(axis=1).sort_index()


def hydro_anomaly(national: pd.Series, issue: datetime) -> float | None:
    """Stored energy relative to the same time of year in earlier years (0 = normal)."""
    if national.empty:
        return None
    cutoff = pd.Timestamp(issue - RESERVOIR_PUBLICATION_LAG).tz_convert("UTC")
    known = national[national.index <= cutoff]
    if known.empty:
        return None
    latest_ts, latest = known.index[-1], float(known.iloc[-1])
    earlier = known[known.index <= latest_ts - pd.Timedelta(days=300)]
    if earlier.empty:
        return None
    day_of_year = latest_ts.dayofyear
    distance = (earlier.index.dayofyear - day_of_year) % 365
    same_season = earlier[(distance <= 10) | (distance >= 355)]
    if same_season.empty:
        return None
    return latest / float(same_season.mean()) - 1.0


# ------------------------------------------------------------------ nuclear


def nuclear_rows(umm_rows: list[dict]) -> list[dict]:
    return [r for r in umm_rows if r.get("kind") == "production" and r.get("nuclear")]


def nuclear_intervals(rows: list[dict], issue: datetime) -> list[tuple[str, datetime, datetime, float]]:
    """Reactor unavailability as it stood at `issue`: nothing published later."""
    out = []
    for row in dedupe_events(known_at(rows, issue)):
        if row.get("outdated"):
            continue
        mw = float(row.get("unavailable_mw") or 0)
        if mw <= 0:
            continue
        out.append((row["unit"], parse_iso(row["event_start"]), parse_iso(row["event_stop"]), mw))
    return out


def nuclear_out_mw(intervals: list[tuple[str, datetime, datetime, float]], start: datetime, end: datetime) -> float:
    """Average MW of Swedish nuclear unavailable over [start, end), by the day.

    Per unit and day the deepest overlapping message counts; overlapping
    messages about one reactor are restatements, not additional outages.
    """
    days = []
    cursor = start
    while cursor < end:
        days.append(cursor + timedelta(hours=12))
        cursor += timedelta(days=1)
    if not days:
        return 0.0
    total = 0.0
    for noon in days:
        per_unit: dict[str, float] = {}
        for unit, lo, hi, mw in intervals:
            if lo <= noon < hi:
                per_unit[unit] = max(per_unit.get(unit, 0.0), mw)
        total += sum(per_unit.values())
    return total / len(days)


# ------------------------------------------------------------------ fuels


def fuel_series(fuel_rows: list[dict]) -> dict[str, pd.Series]:
    """Daily closes per series ('ttf', 'eua'), indexed by date."""
    out: dict[str, pd.Series] = {}
    for series in {r["series"] for r in fuel_rows}:
        points = sorted((date.fromisoformat(r["date"]), float(r["close"])) for r in fuel_rows if r["series"] == series)
        out[series] = pd.Series([v for _, v in points], index=[d for d, _ in points])
    return out


def fuel_close(series: dict[str, pd.Series], name: str, issue: datetime) -> float | None:
    """The last close strictly before the issue date."""
    values = series.get(name)
    if values is None or values.empty:
        return None
    day = to_local(issue).date()
    before = values[values.index < day]
    return float(before.iloc[-1]) if not before.empty else None


def gas_plant_cost(ttf: float | None, eua: float | None) -> float | None:
    if ttf is None or eua is None:
        return None
    return GAS_HEAT_RATE * ttf + GAS_EMISSIONS_T * eua

"""Hourly multi-market dispatch back-test for a utility-scale BESS.

A linear programme per week, normalised to 1 MW of nameplate power, that
co-optimises day-ahead arbitrage with seven capacity products:

    mFRR up / down, aFRR up / down      Svenska kraftnät capacity markets, per zone
    FCR-N, FCR-D up, FCR-D down         national market (SE + DK2)

Every price is the actual hourly clearing price. Nothing is assumed about hit
rates: the battery is a price taker that is paid the marginal price for what it
offers, and how much it can offer is limited two ways:

* physically – power head-room in each direction, and enough stored energy
  (or empty room) at the start of the hour to deliver the product for its
  required duration on top of the spot schedule;
* by market depth – at most `rho x procured volume` per MW installed, where
  `rho = market share / plant MW`. The file holds a grid of rho values and the
  web page interpolates, so a 300 MW plant cannot sell more than the market buys.

What the model does NOT contain, on purpose, and what the page says so:
activation energy (assumed settled at the balancing price, neutral on the
state of charge), price impact of the plant's own bids, intraday trading,
forecast error (perfect foresight inside each week) and outages.

Two families of result are written:

* `coopt`      full co-optimisation for the periods where every market has data
               (calendar 2024, 2025 and the last 12 months);
* `spot_years` arbitrage-only LP for every calendar year since 2015. The page
               uses the ratio between two of these to restate the spot part of a
               co-optimised result for another price year.
* `coopt_extreme` the same co-optimisation with the spot prices of 2022 laid over
               each period's own capacity-market prices: the "extreme year".
               Scaling only the spot part understated it, because a battery that
               sees 2022's spreads moves out of the reserves and into arbitrage.
* `actual_2022` 2022 as it was for a battery: its own spot and FCR prices, aFRR
               from May, and no mFRR capacity market. For reference only. FCR-D
               paid 63 EUR/MW/h then against about 5 now, on a market batteries
               had not yet filled.

Run by hand (needs scipy, see requirements-geo.txt):

    python -m src.geo.dispatch_engine
"""

from __future__ import annotations

import json
import logging
import math
import os
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from typing import Any

import numpy as np

from ..config import ACTUALS_DIR, SITE_DATA_DIR
from ..timeutil import TZ, iso
from . import svk_data
from .market import RTE, ZONES

log = logging.getLogger(__name__)

OUT_PATH = SITE_DATA_DIR / "bess-map" / "dispatch_backtest.json"
VERSION = "2026.10-dispatch-v3"

SOC_MIN, SOC_MAX, SOC_START = 0.05, 0.95, 0.50
# Cycle hurdle in the objective, EUR per MWh discharged. Keeps the LP from
# cycling for a spread that would not pay for the wear. Not deducted from the
# reported revenue: the page charges wear separately from the reported cycles.
WEAR_EUR_MWH = 7.0
BLOCK_H = 168
DURATIONS = (1.0, 2.0, 4.0)
# rho = market share / plant MW. 0 is arbitrage only; 0.06 is effectively uncapped.
RHO_GRID = (0.0, 0.0003, 0.0006, 0.0012, 0.0025, 0.005, 0.01, 0.02, 0.06)
SAMPLE_RHO = 0.0025  # ~ a 40 MW plant taking 10 % of each market

# key, uses up-power, uses down-power, hours of energy up, hours of energy down
RESERVES: tuple[tuple[str, int, int, float, float], ...] = (
    ("mfrr_up", 1, 0, 1.0, 0.0),
    ("mfrr_down", 0, 1, 0.0, 1.0),
    ("afrr_up", 1, 0, 1.0, 0.0),
    ("afrr_down", 0, 1, 0.0, 1.0),
    ("fcr_n", 1, 1, 1.0, 1.0),
    ("fcr_d_up", 1, 0, 1.0 / 3.0, 0.0),
    ("fcr_d_down", 0, 1, 0.0, 1.0 / 3.0),
)
PRODUCTS = ("spot",) + tuple(r[0] for r in RESERVES)
FIRST_FULL_YEAR = 2015
EXTREME_YEAR = 2022


# ------------------------------------------------------------------ the LP
@lru_cache(maxsize=None)
def _structure(n: int, with_reserves: bool):
    """Constraint matrices for a block of n hours. Depends on nothing but n."""
    from scipy import sparse

    eta = math.sqrt(RTE)
    res = RESERVES if with_reserves else ()
    nv = 3 + len(res)  # c, d, soc, reserves...
    c0, d0, s0 = 0, n, 2 * n
    r0 = [3 * n + i * n for i in range(len(res))]
    t = np.arange(n)

    # soc_t - soc_{t-1} - eta*c_t + d_t/eta = 0   (soc_{-1} is a constant on the rhs)
    rows = np.concatenate([t, t[1:], t, t])
    cols = np.concatenate([s0 + t, s0 + t[1:] - 1, c0 + t, d0 + t])
    vals = np.concatenate([np.ones(n), -np.ones(n - 1), -eta * np.ones(n), np.ones(n) / eta])
    a_eq = sparse.csr_matrix((vals, (rows, cols)), shape=(n, nv * n))

    r_, c_, v_ = [], [], []

    def add(block: int, col: np.ndarray, val: float | np.ndarray, hours: np.ndarray = t) -> None:
        r_.append(block * n + hours)
        c_.append(col)
        v_.append(np.broadcast_to(val, hours.shape).astype(float))

    add(0, d0 + t, 1.0)                       # 0: discharge + up reserves <= 1
    add(1, c0 + t, 1.0)                       # 1: charge + down reserves <= 1
    add(2, c0 + t, 1.0); add(2, d0 + t, 1.0)  # 2: charge + discharge <= 1
    add(3, d0 + t, 1.0 / eta)                 # 3: energy to deliver upwards <= soc_{t-1} - min
    add(3, s0 + t[1:] - 1, -1.0, t[1:])
    add(4, c0 + t, eta)                       # 4: energy to absorb downwards <= max - soc_{t-1}
    add(4, s0 + t[1:] - 1, 1.0, t[1:])
    for i, (_, up_p, dn_p, up_e, dn_e) in enumerate(res):
        if up_p:
            add(0, r0[i] + t, 1.0)
        if dn_p:
            add(1, r0[i] + t, 1.0)
        if up_e:
            add(3, r0[i] + t, up_e / eta)
        if dn_e:
            add(4, r0[i] + t, dn_e * eta)
    a_ub = sparse.csr_matrix(
        (np.concatenate(v_), (np.concatenate(r_), np.concatenate(c_))), shape=(5 * n, nv * n)
    )
    return a_eq, a_ub, nv


def solve_block(
    spot: np.ndarray,
    duration_h: float,
    prices: np.ndarray | None = None,
    caps: np.ndarray | None = None,
) -> dict[str, np.ndarray]:
    """Optimal schedule for one block, per MW. `prices`/`caps` are (7, n) or None for arbitrage only."""
    from scipy.optimize import linprog

    n = len(spot)
    with_res = prices is not None
    a_eq, a_ub, nv = _structure(n, with_res)
    e = duration_h
    smin, smax, sstart = SOC_MIN * e, SOC_MAX * e, SOC_START * e

    cost = np.zeros(nv * n)
    cost[0:n] = spot
    cost[n : 2 * n] = -(spot - WEAR_EUR_MWH)
    lo = np.zeros(nv * n)
    hi = np.ones(nv * n)
    lo[2 * n : 3 * n] = smin
    hi[2 * n : 3 * n] = smax
    lo[3 * n - 1] = sstart  # leave the block as full as it was entered
    if with_res:
        cost[3 * n :] = -prices.reshape(-1)
        hi[3 * n :] = np.where(prices.reshape(-1) > 0, np.clip(caps.reshape(-1), 0.0, 1.0), 0.0)

    b_eq = np.zeros(n)
    b_eq[0] = sstart
    b_ub = np.concatenate([np.ones(3 * n), np.full(n, -smin), np.full(n, smax)])
    b_ub[3 * n] = sstart - smin
    b_ub[4 * n] = smax - sstart

    res = linprog(cost, A_ub=a_ub, b_ub=b_ub, A_eq=a_eq, b_eq=b_eq, bounds=np.column_stack([lo, hi]), method="highs")
    if res.status != 0:
        raise RuntimeError(f"dispatch LP failed: {res.message}")
    x = res.x
    out = {"charge": x[0:n], "discharge": x[n : 2 * n], "soc": x[2 * n : 3 * n]}
    if with_res:
        out["reserves"] = x[3 * n :].reshape(len(RESERVES), n)
    return out


def _blocks(n: int) -> list[tuple[int, int]]:
    edges = list(range(0, n, BLOCK_H)) + [n]
    if len(edges) > 2 and edges[-1] - edges[-2] < 48:  # fold a short tail into the last full week
        edges.pop(-2)
    return list(zip(edges[:-1], edges[1:]))


def run_period(
    spot: np.ndarray,
    duration_h: float,
    prices: np.ndarray | None = None,
    volumes: np.ndarray | None = None,
    rho: float = 0.0,
    trace: tuple[int, int] | None = None,
) -> dict[str, Any]:
    """Annualised result per MW for one series. `trace=(i0, i1)` also returns the hourly schedule there."""
    n = len(spot)
    with_res = prices is not None and rho > 0
    caps = np.minimum(1.0, rho * volumes) if with_res else None
    rev = np.zeros(len(PRODUCTS))
    mwh = np.zeros(len(PRODUCTS))   # MW-hours sold per product; for spot: MWh discharged
    hrs = np.zeros(len(PRODUCTS))
    rows: list[list[float]] = []
    for a, b in _blocks(n):
        sol = solve_block(spot[a:b], duration_h, prices[:, a:b] if with_res else None, caps[:, a:b] if with_res else None)
        c, d = sol["charge"], sol["discharge"]
        rev[0] += float(np.sum(spot[a:b] * (d - c)))
        mwh[0] += float(np.sum(d))
        hrs[0] += float(np.sum((d > 0.01) | (c > 0.01)))
        if with_res:
            r = sol["reserves"]
            rev[1:] += np.sum(prices[:, a:b] * r, axis=1)
            mwh[1:] += np.sum(r, axis=1)
            hrs[1:] += np.sum(r > 0.01, axis=1)
        if trace and a < trace[1] and b > trace[0]:
            for i in range(max(a, trace[0]), min(b, trace[1])):
                j = i - a
                row = [float(spot[i]), 100.0 * float(sol["soc"][j]) / duration_h, float(c[j]), float(d[j])]
                row += [float(sol["reserves"][k][j]) for k in range(len(RESERVES))] if with_res else [0.0] * len(RESERVES)
                rows.append(row)
    scale = 8760.0 / n
    out: dict[str, Any] = {
        "rev": [round(float(v) * scale, 1) for v in rev],
        "mw": [round(float(v) / n, 4) for v in mwh],            # average MW held per MW installed
        "hrs": [round(float(v) / n, 4) for v in hrs],           # share of hours with a position
        "cycles": round(float(mwh[0]) * scale / duration_h, 1),  # full equivalent cycles a year
    }
    if trace:
        out["trace"] = rows
    return out


# ------------------------------------------------------------------- data
def load_spot() -> dict[str, dict[datetime, float]]:
    """{zone: {utc_hour: EUR/MWh}} from data/actuals, with the 2020-10..2021-12 hole filled from SvK."""
    out: dict[str, dict[datetime, float]] = {z: {} for z in ZONES}
    for path in sorted(ACTUALS_DIR.glob("*.jsonl")):
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                row = json.loads(line)
                zone, price = row.get("zone"), row.get("price_eur_mwh")
                if zone in out and price is not None:
                    out[zone][datetime.fromisoformat(row["ts"]).astimezone(timezone.utc)] = float(price)
    for zone, hours in svk_data.spot_fill().items():
        for ts, price in hours.items():
            out[zone].setdefault(ts, price)
    return out


def _year_hours(year: int) -> list[datetime]:
    start = datetime(year, 1, 1, tzinfo=TZ).astimezone(timezone.utc)
    end = datetime(year + 1, 1, 1, tzinfo=TZ).astimezone(timezone.utc)
    return [start + timedelta(hours=i) for i in range(int((end - start).total_seconds() // 3600))]


def _series(hours: list[datetime], lookup: dict[datetime, float]) -> tuple[np.ndarray, int]:
    """Values for `hours`; a missing hour is carried forward. Returns (array, number missing)."""
    vals, missing, last = [], 0, None
    for h in hours:
        v = lookup.get(h)
        if v is None:
            missing += 1
            v = last
        last = v if v is not None else last
        vals.append(v)
    first = next((v for v in vals if v is not None), 0.0)
    return np.array([first if v is None else v for v in vals], dtype=float), missing


def _reserve_arrays(hours, zone, mfrr, afrr, fcr) -> tuple[np.ndarray, np.ndarray]:
    """(prices, volumes), each (7, n). An hour without a record had no procurement: price 0, volume 0."""
    src = {
        "mfrr_up": mfrr.get((zone, "up"), {}), "mfrr_down": mfrr.get((zone, "down"), {}),
        "afrr_up": afrr.get((zone, "up"), {}), "afrr_down": afrr.get((zone, "down"), {}),
        "fcr_n": fcr["fcr_n"], "fcr_d_up": fcr["fcr_d_up"], "fcr_d_down": fcr["fcr_d_down"],
    }
    prices = np.zeros((len(RESERVES), len(hours)))
    vols = np.zeros((len(RESERVES), len(hours)))
    for i, (key, *_rest) in enumerate(RESERVES):
        s = src[key]
        for j, h in enumerate(hours):
            rec = s.get(h)
            if rec:
                prices[i, j], vols[i, j] = max(0.0, rec[0]), rec[1]
    return prices, vols


def _aligned_extreme_spot(hours: list[datetime], lookup: dict[datetime, float]) -> np.ndarray:
    """2022's spot price at the same month, day and hour (UTC) as each hour given."""
    values = []
    for h in hours:
        try:
            key = h.replace(year=EXTREME_YEAR)
        except ValueError:  # 29 February
            key = h.replace(year=EXTREME_YEAR, day=28)
        values.append(lookup.get(key, np.nan))
    arr = np.array(values, dtype=float)
    arr[np.isnan(arr)] = np.nanmean(arr)
    return arr


def _task(args: tuple) -> tuple:
    key, spot, dur, prices, vols, rho, trace = args
    return key, run_period(spot, dur, prices, vols, rho, trace)


# ------------------------------------------------------------------ build
def build(workers: int | None = None, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    spot = load_spot()
    mfrr = svk_data.capacity_series("mfrr")
    afrr = svk_data.capacity_series("afrr")
    fcr = svk_data.fcr_series(date(2023, 12, 1), now.date())
    fcr_extreme = svk_data.fcr_series(date(EXTREME_YEAR, 1, 1), date(EXTREME_YEAR, 12, 31))

    # The last 12 months end where both spot and the capacity markets have data.
    last_mfrr = min(max(mfrr[(z, d)]) for z in ZONES for d in ("up", "down"))
    last_spot = min(max(h for h in spot[z] if h <= now) for z in ZONES)
    end = min(last_mfrr, last_spot, max(fcr["fcr_n"])).replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    l12 = [end - timedelta(hours=8760 - i) for i in range(8760)]

    full_years = [
        y for y in range(FIRST_FULL_YEAR, now.astimezone(TZ).year)
        if all(sum(1 for h in _year_hours(y) if h in spot[z]) >= 0.97 * len(_year_hours(y)) for z in ZONES)
    ]
    coopt_periods: dict[str, list[datetime]] = {"last12m": l12}
    for y in (2024, 2025):
        if y in full_years:
            coopt_periods[str(y)] = _year_hours(y)

    tasks: list[tuple] = []
    meta_missing: dict[str, int] = defaultdict(int)
    market: dict[str, Any] = {}
    sample_idx: dict[str, tuple[int, int]] = {}

    # Arbitrage only: every full calendar year, plus the last 12 months as the common denominator.
    spot_periods = {str(y): _year_hours(y) for y in full_years}
    spot_periods["last12m"] = l12
    for pname, hours in spot_periods.items():
        for z in ZONES:
            arr, miss = _series(hours, spot[z])
            meta_missing[pname] += miss
            for dur in DURATIONS:
                tasks.append((("spot", pname, z, dur, 0.0), arr, dur, None, None, 0.0, None))

    for pname, hours in coopt_periods.items():
        market[pname] = {}
        for z in ZONES:
            arr, _ = _series(hours, spot[z])
            prices, vols = _reserve_arrays(hours, z, mfrr, afrr, fcr)
            market[pname][z] = {
                key: {
                    "price_mean": round(float(prices[i].mean()), 2),
                    "price_when_procured": round(float(prices[i][vols[i] > 0].mean()), 2) if (vols[i] > 0).any() else 0.0,
                    "volume_mean_mw": round(float(vols[i].mean()), 1),
                    "hours_share": round(float((vols[i] > 0).mean()), 3),
                    "value_eur_mw_yr": round(float(prices[i].sum()) * 8760.0 / len(hours), 0),
                }
                for i, (key, *_r) in enumerate(RESERVES)
            }
            market[pname][z]["spot"] = {
                "mean": round(float(arr.mean()), 2),
                "neg_share": round(float((arr < 0).mean()), 4),
            }
            for dur in DURATIONS:
                for rho in RHO_GRID[1:]:
                    trace = None
                    if pname == "last12m" and rho == SAMPLE_RHO:
                        # A winter week: the Monday on or after 12 January inside the window.
                        local = [h.astimezone(TZ) for h in hours]
                        i0 = next(i for i, t in enumerate(local) if t.month == 1 and t.day >= 12 and t.weekday() == 0 and t.hour == 0)
                        trace = (i0, i0 + 168)
                        sample_idx[z] = trace
                    tasks.append((("coopt", pname, z, dur, rho), arr, dur, prices, vols, rho, trace))
            # The extreme year for this period: 2022's spot, this period's reserves.
            extreme = _aligned_extreme_spot(hours, spot[z])
            for dur in DURATIONS:
                for rho in RHO_GRID:
                    tasks.append((("extreme", pname, z, dur, rho), extreme, dur, prices, vols, rho, None))

    # 2022 as it was, for reference: its own FCR prices, aFRR from May, no mFRR market.
    hours_2022 = _year_hours(EXTREME_YEAR)
    for z in ZONES:
        arr, _ = _series(hours_2022, spot[z])
        prices, vols = _reserve_arrays(hours_2022, z, {}, afrr, fcr_extreme)
        for dur in DURATIONS:
            for rho in RHO_GRID:
                tasks.append((("actual2022", str(EXTREME_YEAR), z, dur, rho), arr, dur, prices, vols, rho, None))

    log.info("dispatch: %s LP runs", len(tasks))
    workers = workers or max(1, (os.cpu_count() or 2) - 1)
    results: dict[tuple, dict] = {}
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for k, (key, res) in enumerate(pool.map(_task, tasks, chunksize=4)):
            results[key] = res
            if k % 100 == 0:
                log.info("dispatch: %s / %s", k, len(tasks))

    def dkey(d: float) -> str:
        return str(int(d))

    spot_years: dict[str, Any] = {z: {dkey(d): {} for d in DURATIONS} for z in ZONES}
    for (kind, pname, z, dur, _rho), res in results.items():
        if kind == "spot":
            spot_years[z][dkey(dur)][pname] = {"rev": res["rev"][0], "cycles": res["cycles"]}

    coopt: dict[str, Any] = {}
    sample: dict[str, Any] = {}
    for pname in coopt_periods:
        coopt[pname] = {}
        for z in ZONES:
            coopt[pname][z] = {}
            for dur in DURATIONS:
                base = results[("spot", pname, z, dur, 0.0)] if pname in spot_periods else None
                zero = {
                    "rev": [base["rev"][0]] + [0.0] * len(RESERVES), "mw": [0.0] * len(PRODUCTS),
                    "hrs": [0.0] * len(PRODUCTS), "cycles": base["cycles"],
                }
                levels = [zero] + [results[("coopt", pname, z, dur, rho)] for rho in RHO_GRID[1:]]
                coopt[pname][z][dkey(dur)] = {
                    "rev": [lv["rev"] for lv in levels],
                    "mw": [lv["mw"] for lv in levels],
                    "hrs": [lv["hrs"] for lv in levels],
                    "cycles": [lv["cycles"] for lv in levels],
                }
                tr = results[("coopt", pname, z, dur, SAMPLE_RHO)].get("trace") if pname == "last12m" else None
                if tr:
                    i0 = sample_idx[z][0]
                    sample.setdefault(z, {})[dkey(dur)] = {
                        "start": iso(coopt_periods[pname][i0]),
                        "rows": [[round(v, 2) for v in row] for row in tr],
                    }

    def levels(kind: str, pname: str, z: str, dur: float) -> dict[str, Any]:
        runs = [results[(kind, pname, z, dur, rho)] for rho in RHO_GRID]
        return {"rev": [r["rev"] for r in runs], "cycles": [r["cycles"] for r in runs]}

    coopt_extreme = {
        pname: {z: {dkey(d): levels("extreme", pname, z, d) for d in DURATIONS} for z in ZONES}
        for pname in coopt_periods
    }
    actual_2022 = {z: {dkey(d): levels("actual2022", str(EXTREME_YEAR), z, d) for d in DURATIONS} for z in ZONES}

    def span(hours: list[datetime]) -> dict[str, Any]:
        return {"from": iso(hours[0]), "to": iso(hours[-1] + timedelta(hours=1)), "hours": len(hours)}

    periods = {p: span(h) for p, h in coopt_periods.items()}
    try:
        from ..store import load_actuals
        from .reserve_comovement import measure

        comovement = measure(load_actuals(), periods)
    except Exception as exc:  # noqa: BLE001 - the extreme year then keeps reserve prices unchanged
        log.warning("reserve co-movement could not be measured: %s", exc)
        comovement = None

    return {
        "reserve_comovement": comovement,
        "version": VERSION,
        "generated_at": iso(now),
        "model": "Veckovis linjärprogrammering per MW, pristagare, perfekt förutseende inom veckan",
        "assumptions": {
            "rte": RTE, "soc_min": SOC_MIN, "soc_max": SOC_MAX, "wear_hurdle_eur_mwh": WEAR_EUR_MWH,
            "block_hours": BLOCK_H,
            "reserve_energy_hours": {r[0]: max(r[3], r[4]) for r in RESERVES},
            "not_modelled": [
                "aktiveringsenergi", "egen prispåverkan", "intradag", "prognosfel", "otillgänglighet",
            ],
        },
        "products": list(PRODUCTS),
        "durations": [int(d) for d in DURATIONS],
        "rho_grid": list(RHO_GRID),
        "sample_rho": SAMPLE_RHO,
        "sample_cols": ["spot", "soc_pct", "charge", "discharge"] + [r[0] for r in RESERVES],
        "periods": periods,
        "full_years": full_years,
        "spot_missing_hours": dict(meta_missing),
        "sources": {
            "spot": "ENTSO-E / Nord Pool day-ahead (data/actuals); 2020-10–2021-12 från Svenska kraftnät Data Service",
            "mfrr": "Svenska kraftnät Data Service, mfrr_capacity_market (marginalpris EUR/MW och volym per elområde och timme)",
            "afrr": "Svenska kraftnät Data Service, afrr_capacity_market",
            "fcr": "Svenska kraftnät Mimer, FCR-N / FCR-D upp / FCR-D ned (volymvägt pris, total volym SE + DK2)",
        },
        "market": market,
        "spot_years": spot_years,
        "coopt": coopt,
        "extreme_year": EXTREME_YEAR,
        "coopt_extreme": coopt_extreme,
        "actual_2022": actual_2022,
        "sample_week": sample,
    }


def main() -> None:  # pragma: no cover - manual build
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    payload = build()
    OUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print("written", OUT_PATH, OUT_PATH.stat().st_size, "bytes; full years", payload["full_years"])


if __name__ == "__main__":  # pragma: no cover
    main()

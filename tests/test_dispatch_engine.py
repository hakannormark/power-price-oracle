"""The dispatch LP: physics, market cap and the published file's invariants."""

from __future__ import annotations

import json
import math

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("scipy")

from src.config import SITE_DATA_DIR  # noqa: E402
from src.geo import dispatch_engine as de  # noqa: E402
from src.geo.market import RTE  # noqa: E402

N = 336
ETA = math.sqrt(RTE)


def _square() -> "np.ndarray":
    return np.tile(np.r_[np.full(12, 10.0), np.full(12, 110.0)], N // 24)


def test_flat_prices_earn_nothing():
    res = de.run_period(np.full(N, 50.0), 2.0)
    assert res["rev"][0] == 0.0
    assert res["cycles"] == 0.0


def test_spread_below_wear_hurdle_is_not_cycled():
    # 3 EUR of spread does not pay for 7 EUR/MWh of wear plus the losses.
    spot = np.tile(np.r_[np.full(12, 50.0), np.full(12, 53.0)], N // 24)
    assert de.run_period(spot, 2.0)["cycles"] == 0.0


def test_arbitrage_matches_hand_calculation():
    # One full cycle a day between 5 % and 95 %: buy 0.9*E/eta at 10, sell 0.9*E*eta at 110.
    dur = 2.0
    per_day = 0.9 * dur * ETA * 110.0 - 0.9 * dur / ETA * 10.0
    res = de.run_period(_square(), dur)
    # Each week is entered and left half full, which costs about half a cycle a week.
    assert 0.90 * per_day * 365 <= res["rev"][0] <= per_day * 365


def test_schedule_respects_physics():
    spot = _square()
    prices = np.full((len(de.RESERVES), 168), 15.0)
    caps = np.full((len(de.RESERVES), 168), 1.0)
    for dur in (1.0, 2.0, 4.0):
        sol = de.solve_block(spot[:168], dur, prices, caps)
        c, d, soc, r = sol["charge"], sol["discharge"], sol["soc"], sol["reserves"]
        up_p = np.array([x[1] for x in de.RESERVES])[:, None]
        dn_p = np.array([x[2] for x in de.RESERVES])[:, None]
        up_e = np.array([x[3] for x in de.RESERVES])[:, None]
        dn_e = np.array([x[4] for x in de.RESERVES])[:, None]
        tol = 1e-6
        assert (soc >= de.SOC_MIN * dur - tol).all() and (soc <= de.SOC_MAX * dur + tol).all()
        # The same megawatt is never sold twice.
        assert (d + (up_p * r).sum(axis=0) <= 1 + tol).all()
        assert (c + (dn_p * r).sum(axis=0) <= 1 + tol).all()
        assert (c + d <= 1 + tol).all()
        # The energy behind every commitment is there at the start of the hour.
        prev = np.r_[de.SOC_START * dur, soc[:-1]]
        assert ((d + (up_e * r).sum(axis=0)) / ETA <= prev - de.SOC_MIN * dur + tol).all()
        assert ((c + (dn_e * r).sum(axis=0)) * ETA <= de.SOC_MAX * dur - prev + tol).all()
        # State of charge follows from the schedule.
        np.testing.assert_allclose(soc - prev, ETA * c - d / ETA, atol=1e-6)
        assert soc[-1] >= de.SOC_START * dur - tol


def test_market_cap_limits_what_is_sold():
    spot = np.full(N, 50.0)
    prices = np.zeros((len(de.RESERVES), N))
    vols = np.zeros((len(de.RESERVES), N))
    prices[0], vols[0] = 20.0, 100.0  # mFRR up only, 100 MW procured
    capped = de.run_period(spot, 2.0, prices, vols, rho=0.002)   # at most 0.2 MW per MW
    free = de.run_period(spot, 2.0, prices, vols, rho=0.06)
    assert capped["mw"][1] == pytest.approx(0.2, abs=1e-3)
    assert capped["rev"][1] == pytest.approx(0.2 * 20.0 * 8760, rel=1e-3)
    assert free["rev"][1] > capped["rev"][1]
    # Nothing is sold where nothing was procured.
    assert all(v == 0.0 for v in capped["mw"][2:])
    assert de.run_period(spot, 2.0, prices, vols, rho=0.0)["rev"][1] == 0.0


def test_one_hour_battery_cannot_sell_full_power_both_ways():
    spot = np.full(N, 50.0)
    prices = np.zeros((len(de.RESERVES), N))
    vols = np.full((len(de.RESERVES), N), 1e6)
    prices[0] = prices[1] = 10.0  # mFRR up and down, an hour of energy each
    res = de.run_period(spot, 1.0, prices, vols, rho=0.06)
    assert res["mw"][1] + res["mw"][2] <= 0.9 * ETA + 0.9 / ETA + 1e-3
    assert res["mw"][1] < 1.0 and res["mw"][2] < 1.0


# --------------------------------------------------------- the published file
@pytest.fixture(scope="module")
def published() -> dict:
    return json.loads((SITE_DATA_DIR / "bess-map" / "dispatch_backtest.json").read_text(encoding="utf-8"))


def test_published_periods_are_what_they_say(published):
    assert published["periods"]["last12m"]["hours"] == 8760
    for year in ("2024", "2025"):
        span = published["periods"][year]
        assert span["from"].startswith(f"{year}-01-01") and span["to"].startswith(f"{int(year) + 1}-01-01")
    years = published["full_years"]
    assert years == list(range(years[0], years[-1] + 1)), "full years must be contiguous"
    assert 2022 in years


def test_extreme_year_is_the_highest_arbitrage_year_everywhere(published):
    for zone, by_dur in published["spot_years"].items():
        for dur, years in by_dur.items():
            best = max(years, key=lambda k: years[k]["rev"])
            assert best == "2022", f"{zone} {dur}h: {best} beats 2022"


def test_published_results_are_monotone(published):
    spot_i = published["products"].index("spot")
    for period, zones in published["coopt"].items():
        for zone, by_dur in zones.items():
            totals = {}
            for dur, e in by_dur.items():
                tot = [sum(level) for level in e["rev"]]
                # A looser market cap never lowers the optimum.
                assert all(b >= a - 1.0 for a, b in zip(tot, tot[1:])), (period, zone, dur)
                # rho = 0 is exactly the arbitrage-only run.
                assert e["rev"][0][spot_i] == published["spot_years"][zone][dur][period]["rev"]
                assert sum(e["rev"][0]) == e["rev"][0][spot_i]
                totals[int(dur)] = tot
            # More storage never earns less.
            for a, b in ((1, 2), (2, 4)):
                assert all(y >= x - 1.0 for x, y in zip(totals[a], totals[b])), (period, zone)


def test_published_market_stats_cover_every_product(published):
    for period, zones in published["market"].items():
        for zone, stats in zones.items():
            for key, *_ in de.RESERVES:
                assert stats[key]["price_mean"] >= 0
                assert 0 <= stats[key]["hours_share"] <= 1

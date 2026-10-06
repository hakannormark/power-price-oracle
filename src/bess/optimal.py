"""Cost-minimising operation of a home battery, by dynamic programming.

The household buys at the import price and sells at the export price. Given
both, its load and its solar production for every hour, there is one cheapest
way to run the battery, and what the battery is worth is the bill without it
minus the bill with it. Nothing else is added on top.

That replaces two estimates that were computed side by side and summed:

* "solar savings" as stored kWh times the full import price, which forgot that
  the same kWh would have earned the export price had it not been stored;
* "arbitrage" as the day's price spread times 70 % of the battery, every day,
  whether or not the battery was already full of solar that day.

Together they counted about 460 full cycles a year on one battery. A simulated
schedule existed but its flows were not what was valued.

The state is the stored energy on an even grid; a step moves between two grid
points. Solved backwards over the whole series, then walked forwards. Prices for
tomorrow are published and a household's load and sun are forecastable a day
ahead, so foresight within the day is realistic; a battery of a few hours gains
nothing from seeing further.

The same algorithm is implemented in site/assets/bess-dispatch.js for the
calculator; tests/test_bess_dispatch_js.js checks the two against each other.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

LEVELS = 49
# Per kWh taken out of the battery. Not a cost in the result: it only keeps the
# optimum from cycling for a gain smaller than the wear it causes.
WEAR_HURDLE_SEK_PER_KWH = 0.05
_INFEASIBLE = 1e12


@dataclass
class Schedule:
    cost: float               # the bill over the series, SEK (hurdle excluded)
    grid_import: np.ndarray   # kWh per hour
    grid_export: np.ndarray
    soc: np.ndarray           # kWh stored at the end of each hour, above the lower bound
    charged: np.ndarray       # kWh into the battery (AC side)
    discharged: np.ndarray    # kWh out of the battery (AC side)


def bill_without_battery(load: np.ndarray, pv: np.ndarray, import_price: np.ndarray, export_price: np.ndarray) -> float:
    net = load - pv
    return float(np.sum(np.where(net > 0, net * import_price, net * export_price)))


def optimal_schedule(
    load: np.ndarray,
    pv: np.ndarray,
    import_price: np.ndarray,
    export_price: np.ndarray,
    window_kwh: float,
    max_power_kw: float,
    round_trip_eff: float,
    allow_grid_charging: bool = True,
    levels: int = LEVELS,
) -> Schedule:
    """The cheapest schedule for a battery with `window_kwh` of usable storage.

    With `allow_grid_charging=False` the battery may only take surplus solar,
    which gives the value of solar storage alone.
    """
    load = np.asarray(load, dtype=float)
    pv = np.asarray(pv, dtype=float)
    imp = np.asarray(import_price, dtype=float)
    exp = np.asarray(export_price, dtype=float)
    n = len(load)
    net = load - pv
    if window_kwh <= 0 or max_power_kw <= 0 or n == 0:
        zero = np.zeros(n)
        return Schedule(bill_without_battery(load, pv, imp, exp), np.maximum(net, 0), np.maximum(-net, 0), zero, zero, zero)

    eta = float(np.sqrt(round_trip_eff))
    grid = np.linspace(0.0, window_kwh, levels)
    delta = grid[None, :] - grid[:, None]                    # stored after minus before
    ac = np.where(delta > 0, delta / eta, delta * eta)       # what the battery draws from (+) or gives (-) the house
    feasible = np.abs(ac) <= max_power_kw + 1e-9
    hurdle = np.where(delta < 0, -ac, 0.0) * WEAR_HURDLE_SEK_PER_KWH
    penalty = np.where(feasible, hurdle, _INFEASIBLE)
    charging = delta > 0

    value = np.zeros(levels)
    choice = np.zeros((n, levels), dtype=np.int16)
    for t in range(n - 1, -1, -1):
        flow = net[t] + ac
        cost = np.where(flow > 0, flow * imp[t], flow * exp[t]) + penalty + value[None, :]
        if not allow_grid_charging:
            # Charging may use the surplus and nothing more.
            cost = np.where(charging & (ac > max(-net[t], 0.0) + 1e-9), _INFEASIBLE, cost)
        choice[t] = np.argmin(cost, axis=1)
        value = cost[np.arange(levels), choice[t]]

    state = 0  # starts empty, and nothing is owed at the end
    soc = np.zeros(n)
    flow_out = np.zeros(n)
    for t in range(n):
        nxt = int(choice[t, state])
        flow_out[t] = ac[state, nxt]
        soc[t] = grid[nxt]
        state = nxt

    grid_flow = net + flow_out
    bill = float(np.sum(np.where(grid_flow > 0, grid_flow * imp, grid_flow * exp)))
    return Schedule(
        cost=bill,
        grid_import=np.maximum(grid_flow, 0.0),
        grid_export=np.maximum(-grid_flow, 0.0),
        soc=soc,
        charged=np.maximum(flow_out, 0.0),
        discharged=np.maximum(-flow_out, 0.0),
    )

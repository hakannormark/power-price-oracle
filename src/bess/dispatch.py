"""Chronological 8760h battery dispatch simulator and revenue stacker.

Models physical state-of-charge (SoC), hardware power limits (C-rate),
round-trip efficiency, and the three competing revenue streams:
1. Solar PV self-consumption (saving retail electricity cost: spot + tax + grid fee)
2. Spot price arbitrage (charging in low hours, discharging in peaks)
3. Grid ancillary services / FCR-D (bidding capacity within remaining SoC window)

Enforces 2026 post-reform rules:
- Abolished 60-öre tax credit: exported electricity yields ONLY spot + grid benefit.
- Value of stored solar kWh: ~1.65 kr/kWh (avoided import cost).
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any
import numpy as np
import pandas as pd

from .optimal import bill_without_battery, optimal_schedule
from .offers import BatteryOffer

# Swedish electricity retail tax & grid components (SE4 2026 typical levels in SEK/kWh)
ENERGY_TAX_SEK = 0.535        # Energy tax incl. 25% VAT (~42.8 öre ex. VAT -> 53.5 öre inc. VAT)
GRID_TRANSFER_FEE_SEK = 0.280 # Variable grid transfer fee incl. VAT
GRID_BENEFIT_EXPORT_SEK = 0.060  # "Nätnytta" compensation for exported solar (approx 6 öre/kWh)
RETAIL_MARGIN_SEK = 0.040     # Supplier fee / margin incl. VAT

# Ancillary service (FCR-D / aggregator) benchmarks in SEK per kW and month
FCR_D_RATE_MIXED_SEK_PER_KW_MONTH = 25.0  # Realistic blended rate after aggregator split and SoC locking
FCR_D_RATE_FULL_SEK_PER_KW_MONTH = 52.0   # Full dedicated bidding (locks SoC, foregoes solar/arbitrage)


@dataclass
class DispatchResult:
    solar_self_consumed_kwh: float
    solar_exported_kwh: float
    grid_imported_kwh: float
    arbitrage_charged_kwh: float
    arbitrage_discharged_kwh: float
    battery_throughput_kwh: float
    equivalent_cycles: float
    solar_savings_sek: float
    arbitrage_profit_sek: float
    ancillary_revenue_sek: float
    total_annual_value_sek: float
    hourly_df: pd.DataFrame


# Share of the theoretical optimum a real controller is assumed to reach. The
# optimum knows the household's load and sun for the day exactly; prices it
# does know, they are published. Stated on the page.
REALISATION = 0.90
SPOT_VAT = 1.25  # a household pays VAT on the spot price it imports at, and gets none on what it exports
# Share of the usable window kept free for frequency response, per strategy.
FCR_WINDOW_RESERVE = {"energy_only": 0.0, "mixed": 0.20, "fcr_priority": 0.40}
MODEL_VERSION = "optimal-v1"


def household_prices(spot_sek_kwh: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(import, export) price per kWh for a household, from the spot price ex VAT."""
    spot = np.asarray(spot_sek_kwh, dtype=np.float64)
    import_price = spot * SPOT_VAT + ENERGY_TAX_SEK + GRID_TRANSFER_FEE_SEK + RETAIL_MARGIN_SEK
    export_price = spot + GRID_BENEFIT_EXPORT_SEK
    return import_price, export_price


def simulate_battery_dispatch(
    profile_df: pd.DataFrame,
    spot_prices_sek_kwh: pd.Series | np.ndarray,
    offer: BatteryOffer,
    strategy: str = "mixed",  # "mixed" (solar + arbitrage + FCR), "energy_only" (solar + arbitrage), "fcr_priority"
) -> DispatchResult:
    """What the battery is worth over the window: the bill without it minus the bill with it.

    The energy value comes from the cheapest schedule (optimal.py), so solar
    storage and price arbitrage share one battery and one set of cycles.
    Solar is the value with grid charging forbidden; arbitrage is what
    allowing it adds. Frequency response is a rate per kW, paid for keeping part
    of the storage window free.
    """
    n = len(profile_df)
    load = profile_df["load_kwh"].to_numpy(dtype=np.float64)
    pv = profile_df["pv_kwh"].to_numpy(dtype=np.float64)
    import_price, export_price = household_prices(np.asarray(spot_prices_sek_kwh, dtype=np.float64))

    window = offer.usable_kwh * (1.0 - FCR_WINDOW_RESERVE.get(strategy, 0.0))
    power = offer.battery_max_power_kw
    baseline = bill_without_battery(load, pv, import_price, export_price)
    solar_only = optimal_schedule(load, pv, import_price, export_price, window, power, offer.round_trip_eff, allow_grid_charging=False)
    if strategy == "fcr_priority":
        full = solar_only  # the window is committed to frequency response; no price arbitrage
    else:
        full = optimal_schedule(load, pv, import_price, export_price, window, power, offer.round_trip_eff)

    scale_to_year = 8760.0 / n if n > 0 else 1.0
    solar_savings_sek = max(0.0, baseline - solar_only.cost) * REALISATION * scale_to_year
    arbitrage_profit_sek = max(0.0, solar_only.cost - full.cost) * REALISATION * scale_to_year

    bid_kw = offer.battery_max_power_kw
    if strategy == "mixed":
        ancillary_revenue_sek = bid_kw * FCR_D_RATE_MIXED_SEK_PER_KW_MONTH * 12.0
    elif strategy == "fcr_priority":
        ancillary_revenue_sek = bid_kw * FCR_D_RATE_FULL_SEK_PER_KW_MONTH * 12.0
    else:
        ancillary_revenue_sek = 0.0

    surplus = np.maximum(pv - load, 0.0)
    charge_solar = np.minimum(full.charged, surplus)
    charge_grid = full.charged - charge_solar
    throughput_kwh = float(full.charged.sum()) * scale_to_year
    total_annual_value_sek = solar_savings_sek + arbitrage_profit_sek + ancillary_revenue_sek

    res_df = pd.DataFrame({
        "ts": profile_df["ts"],
        "soc": full.soc,
        "charge_solar": charge_solar,
        "charge_grid": charge_grid,
        "discharge": full.discharged,
        "grid_export": full.grid_export,
        "grid_import": full.grid_import,
    })
    return DispatchResult(
        solar_self_consumed_kwh=round(float(charge_solar.sum()) * scale_to_year, 1),
        solar_exported_kwh=round(float(full.grid_export.sum()) * scale_to_year, 1),
        grid_imported_kwh=round(float(full.grid_import.sum()) * scale_to_year, 1),
        arbitrage_charged_kwh=round(float(charge_grid.sum()) * scale_to_year, 1),
        arbitrage_discharged_kwh=round(float(charge_grid.sum()) * scale_to_year * offer.round_trip_eff, 1),
        battery_throughput_kwh=round(throughput_kwh, 1),
        equivalent_cycles=round(throughput_kwh / offer.capacity_kwh if offer.capacity_kwh > 0 else 0.0, 1),
        solar_savings_sek=round(solar_savings_sek, 0),
        arbitrage_profit_sek=round(arbitrage_profit_sek, 0),
        ancillary_revenue_sek=round(ancillary_revenue_sek, 0),
        total_annual_value_sek=round(total_annual_value_sek, 0),
        hourly_df=res_df,
    )

"""Lifecycle valuation, extreme-year modeling, and investment payback for BESS.

Models 10-15 year cash flows, battery degradation, market cannibalization (declining
FCR-D reserves due to BESS saturation), extreme year shocks (gas crisis vs wet year),
and tax deduction optimization (1 vs 2 owners, multi-year modular expansion).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
import numpy as np

from .offers import BatteryOffer
from .dispatch import DispatchResult


DISCOUNT_RATE = 0.05  # 5 % real discount rate (kalkylränta)
ANNUAL_DEGRADATION_RATE = 0.018  # 1.8 % annual capacity fade (73 % capacity at year 15)
FCR_CANNIBALIZATION_DECAY = 0.12  # 12 % annual drop in ancillary services as market saturates


@dataclass
class LifecycleCashFlow:
    year: int
    solar_savings: float
    arbitrage_profit: float
    ancillary_revenue: float
    total_revenue: float
    capacity_retention: float
    extreme_multiplier: float
    net_cash_flow: float
    discounted_cash_flow: float
    cumulative_cash_flow: float


EXTREME_YEAR = 2022
NEGATIVE_FACTOR = 0.6
FALLBACK_POSITIVE_FACTOR = 2.3  # only when the extreme year cannot be valued on its own prices


@dataclass
class LifecycleAnalysis:
    offer_id: str
    scenario: str
    num_owners: int
    net_investment: float
    gross_investment: float
    deduction_utilized: float
    deduction_lost: float
    payback_years: float | None
    discounted_payback_years: float | None
    npv_10y: float
    npv_15y: float
    irr: float | None
    annual_cash_flows: list[LifecycleCashFlow]


def compute_lifecycle(
    offer: BatteryOffer,
    dispatch_res: DispatchResult,
    num_owners: int = 2,
    scenario: str = "nordic_frequency",  # "base_only", "nordic_frequency", "cannibalization"
    horizon_years: int = 15,
    extreme: tuple[float, float] | None = None,
) -> LifecycleAnalysis:
    """Compute 10-15 year lifecycle economics for a battery offer.

    `extreme` is (solar value, arbitrage value) for the same system in the
    extreme year, valued on that year's own hourly prices and sun. Without it
    the positive extreme year falls back to the fixed factor.
    """
    net_inv = offer.net_price(num_owners)
    gross_inv = offer.gross_price
    ded_lost = offer.deduction_lost(num_owners)
    ded_used = gross_inv * 0.485 - ded_lost

    base_solar = dispatch_res.solar_savings_sek
    base_arb = dispatch_res.arbitrage_profit_sek
    base_fcr = dispatch_res.ancillary_revenue_sek

    # Which years are extreme. A positive one is 2022 as it was in the zone
    # (see `extreme`); a negative one is a stress assumption, 0.6 x the energy
    # value, because no year since 2015 has been that weak for arbitrage.
    # Frequency follows the page: 1.5 positive and 0.5 negative per decade.
    kinds = ["normal"] * horizon_years
    if scenario == "nordic_frequency":
        for index, kind in ((2, "positive"), (6, "negative"), (10, "positive")):
            if horizon_years > index:
                kinds[index] = kind
    if extreme is not None:
        extreme_solar, extreme_arb = extreme
    else:
        extreme_solar, extreme_arb = base_solar * (1 + (FALLBACK_POSITIVE_FACTOR - 1) * 0.2), base_arb * FALLBACK_POSITIVE_FACTOR
    energy_base = base_solar + base_arb
    positive_ratio = (extreme_solar + extreme_arb) / energy_base if energy_base > 0 else 1.0

    cash_flows: list[LifecycleCashFlow] = []
    cumulative = -net_inv
    payback_y: float | None = None
    disc_payback_y: float | None = None
    disc_cumulative = -net_inv

    yearly_net_cf = []

    for y in range(1, horizon_years + 1):
        idx = y - 1
        # Capacity fade affects throughput/storage
        retention = max(0.65, 1.0 - (y - 1) * ANNUAL_DEGRADATION_RATE)
        kind = kinds[idx]
        m_ext = positive_ratio if kind == "positive" else (NEGATIVE_FACTOR if kind == "negative" else 1.0)

        # Energy value: the year's own valuation in a positive extreme year, a
        # flat factor in a negative one, the base year otherwise. An earlier
        # version multiplied every normal year by 0.8 here by mistake.
        if kind == "positive":
            solar_rev, arb_rev = extreme_solar * retention, extreme_arb * retention
        elif kind == "negative":
            solar_rev, arb_rev = base_solar * retention * NEGATIVE_FACTOR, base_arb * retention * NEGATIVE_FACTOR
        else:
            solar_rev, arb_rev = base_solar * retention, base_arb * retention

        # Ancillary services: subject to battery capacity fade (SoH) and market saturation/cannibalization
        if scenario == "cannibalization":
            fcr_decay = max(0.25, (1.0 - FCR_CANNIBALIZATION_DECAY) ** (y - 1))
        elif scenario == "nordic_frequency":
            # Realistic Nordic baseline: 5 % annual erosion as BESS volume enters, stabilizing at 40 % floor
            fcr_decay = max(0.40, (0.95) ** (y - 1))
        else:
            fcr_decay = 1.0
        fcr_rev = base_fcr * retention * fcr_decay * (1.2 if kind == "positive" else (0.8 if kind == "negative" else 1.0))

        tot_rev = solar_rev + arb_rev + fcr_rev
        net_cf = tot_rev
        yearly_net_cf.append(net_cf)

        disc_cf = net_cf / ((1.0 + DISCOUNT_RATE) ** y)

        prev_cum = cumulative
        cumulative += net_cf
        if prev_cum < 0 and cumulative >= 0 and payback_y is None:
            # Linear interpolation for fractional payback year
            payback_y = round((y - 1) + (-prev_cum) / net_cf, 1)

        prev_disc_cum = disc_cumulative
        disc_cumulative += disc_cf
        if prev_disc_cum < 0 and disc_cumulative >= 0 and disc_payback_y is None:
            disc_payback_y = round((y - 1) + (-prev_disc_cum) / disc_cf, 1)

        cash_flows.append(
            LifecycleCashFlow(
                year=y,
                solar_savings=round(solar_rev, 0),
                arbitrage_profit=round(arb_rev, 0),
                ancillary_revenue=round(fcr_rev, 0),
                total_revenue=round(tot_rev, 0),
                capacity_retention=round(retention, 3),
                extreme_multiplier=round(m_ext, 2),
                net_cash_flow=round(net_cf, 0),
                discounted_cash_flow=round(disc_cf, 0),
                cumulative_cash_flow=round(cumulative, 0),
            )
        )

    # 10-year and 15-year Net Present Value (NPV)
    npv_10 = -net_inv + sum(cf.discounted_cash_flow for cf in cash_flows[:10])
    npv_15 = -net_inv + sum(cf.discounted_cash_flow for cf in cash_flows)

    # Internal Rate of Return (IRR)
    irr: float | None = None
    try:
        # np.irr replacement
        import numpy_financial as npf  # type: ignore
        irr = float(npf.irr([-net_inv] + yearly_net_cf))
    except Exception:
        # Simple secant solver fallback for IRR
        def npv_func(r):
            return -net_inv + sum(cf / ((1.0 + r) ** (i + 1)) for i, cf in enumerate(yearly_net_cf))
        r0, r1 = 0.05, 0.15
        for _ in range(30):
            f0, f1 = npv_func(r0), npv_func(r1)
            if abs(f1 - f0) < 1e-6:
                break
            r2 = r1 - f1 * (r1 - r0) / (f1 - f0)
            r0, r1 = r1, r2
        if 0.0 < r1 < 1.0:
            irr = round(r1, 4)

    return LifecycleAnalysis(
        offer_id=offer.id,
        scenario=scenario,
        num_owners=num_owners,
        net_investment=round(net_inv, 0),
        gross_investment=round(gross_inv, 0),
        deduction_utilized=round(ded_used, 0),
        deduction_lost=round(ded_lost, 0),
        payback_years=payback_y,
        discounted_payback_years=disc_payback_y,
        npv_10y=round(npv_10, 0),
        npv_15y=round(npv_15, 0),
        irr=irr,
        annual_cash_flows=cash_flows,
    )

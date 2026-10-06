"""Builds and serializes BESS valuation and battery investment data for the website and API."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from ..config import SITE_DATA_DIR
from ..publish.api import write_json
from ..timeutil import TZ, iso, now_local
from .backtest import run_historical_backtest
from .dispatch import household_prices, simulate_battery_dispatch
from .lifecycle import EXTREME_YEAR, compute_lifecycle
from .offers import OFFERS, get_offer
from .profiles import CASE_HOUSE_PV_KWP_EFFECTIVE, generate_household_profiles

log = logging.getLogger(__name__)


ZONE_NAMES = {
    "SE1": "SE1 (Luleå / Norra Sverige)",
    "SE2": "SE2 (Sundsvall / Norra Mellansverige)",
    "SE3": "SE3 (Stockholm / Södra Mellansverige)",
    "SE4": "SE4 (Malmö / Sydsverige)",
}


def _build_single_zone_data(
    actuals: list[dict],
    fx_rate: float = 11.33,
    zone: str = "SE4",
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or now_local()
    zone_rows = [r for r in actuals if r.get("zone") == zone]
    if zone_rows:
        import pandas as pd
        df_z = pd.DataFrame(zone_rows)
        df_z["ts"] = pd.to_datetime(df_z["ts"], utc=True).dt.tz_convert(TZ)
        df_z = df_z.sort_values("ts")
        df_recent = df_z.tail(8760)
        timestamps = [t.to_pydatetime() for t in df_recent["ts"]]
        spot_sek_kwh = (df_recent["price_eur_mwh"] * fx_rate) / 1000.0
    else:
        from datetime import timedelta
        base_t = now - timedelta(days=365)
        timestamps = [base_t + timedelta(hours=i) for i in range(8760)]
        spot_sek_kwh = [0.85] * 8760

    profile_df = generate_household_profiles(timestamps, zone=zone)
    # The calculator in the browser runs the same optimisation on the same hours.
    # Weights sum to a million over the window, so load = weight * annual kWh / 1e6.
    n_hours = len(profile_df)

    def _hourly_block(profile: Any, spot: Any) -> dict[str, Any]:
        return {
            "spot": [round(float(v), 4) for v in spot],
            "w_load": [round(float(v), 1) for v in profile["load_kwh"] / profile["load_kwh"].sum() * 1e6],
            # Production per installed kWp at the standard system factor.
            "pv_per_kwp": [round(float(v), 4) for v in profile["pv_kwh"] / CASE_HOUSE_PV_KWP_EFFECTIVE],
        }

    hourly = _hourly_block(profile_df, spot_sek_kwh)
    hourly["from"] = iso(timestamps[0]) if timestamps else None
    hourly["hours"] = n_hours

    # The extreme year, as it was: 2022's hourly prices and 2022's sun in this zone.
    extreme_hourly = None
    if zone_rows:
        df_ext = df_z[df_z["ts"].dt.year == EXTREME_YEAR]
        if len(df_ext) >= 8000:
            ext_timestamps = [t.to_pydatetime() for t in df_ext["ts"]]
            ext_spot = (df_ext["price_eur_mwh"] * fx_rate) / 1000.0
            extreme_hourly = _hourly_block(generate_household_profiles(ext_timestamps, zone=zone), ext_spot)
    hourly["extreme"] = extreme_hourly

    # The back-test already values every offer on every year; its 2022 is the
    # extreme year the lifecycle uses.
    backtest_data = run_historical_backtest(actuals=actuals, fx_rate=fx_rate, zone=zone)
    extreme_offers = (((backtest_data.get("years") or {}).get(str(EXTREME_YEAR)) or {}).get("offers")) or {}

    def _extreme(offer_id: str) -> tuple[float, float] | None:
        row = (extreme_offers.get(offer_id) or {}).get("mixed")
        return (float(row["solar_savings_sek"]), float(row["arbitrage_profit_sek"])) if row else None

    offers_data: list[dict[str, Any]] = []
    for offer in OFFERS:
        disp_mixed = simulate_battery_dispatch(profile_df, spot_sek_kwh, offer, strategy="mixed")
        disp_energy = simulate_battery_dispatch(profile_df, spot_sek_kwh, offer, strategy="energy_only")

        lc_nordic_2own = compute_lifecycle(offer, disp_mixed, num_owners=2, scenario="nordic_frequency", extreme=_extreme(offer.id))
        lc_nordic_1own = compute_lifecycle(offer, disp_mixed, num_owners=1, scenario="nordic_frequency", extreme=_extreme(offer.id))
        lc_base_2own = compute_lifecycle(offer, disp_mixed, num_owners=2, scenario="base_only")
        lc_base_1own = compute_lifecycle(offer, disp_mixed, num_owners=1, scenario="base_only")
        lc_cannibal_2own = compute_lifecycle(offer, disp_mixed, num_owners=2, scenario="cannibalization")
        lc_cannibal_1own = compute_lifecycle(offer, disp_mixed, num_owners=1, scenario="cannibalization")

        def _fmt_lc(lc: Any) -> dict[str, Any]:
            return {
                "payback_years": lc.payback_years,
                "discounted_payback_years": lc.discounted_payback_years,
                "npv_10y": lc.npv_10y,
                "npv_15y": lc.npv_15y,
                "irr": lc.irr,
                "cash_flows": [cf.__dict__ for cf in lc.annual_cash_flows],
            }

        offer_entry = offer.to_dict(num_owners=2)
        offer_entry["annual_dispatch"] = {
            "mixed": {
                "solar_savings_sek": disp_mixed.solar_savings_sek,
                "arbitrage_profit_sek": disp_mixed.arbitrage_profit_sek,
                "ancillary_revenue_sek": disp_mixed.ancillary_revenue_sek,
                "total_annual_value_sek": disp_mixed.total_annual_value_sek,
                "solar_self_consumed_kwh": disp_mixed.solar_self_consumed_kwh,
                "solar_exported_kwh": disp_mixed.solar_exported_kwh,
                "battery_throughput_kwh": disp_mixed.battery_throughput_kwh,
                "equivalent_cycles": disp_mixed.equivalent_cycles,
            },
            "energy_only": {
                "solar_savings_sek": disp_energy.solar_savings_sek,
                "arbitrage_profit_sek": disp_energy.arbitrage_profit_sek,
                "total_annual_value_sek": disp_energy.total_annual_value_sek,
                "solar_self_consumed_kwh": disp_energy.solar_self_consumed_kwh,
                "equivalent_cycles": disp_energy.equivalent_cycles,
            },
        }
        offer_entry["lifecycle"] = {
            "nordic_frequency_2_owners": _fmt_lc(lc_nordic_2own),
            "nordic_frequency_1_owner": _fmt_lc(lc_nordic_1own),
            "base_only_2_owners": _fmt_lc(lc_base_2own),
            "base_only_1_owner": _fmt_lc(lc_base_1own),
            "cannibalization_2_owners": _fmt_lc(lc_cannibal_2own),
            "cannibalization_1_owner": _fmt_lc(lc_cannibal_1own),
        }
        offers_data.append(offer_entry)

    import numpy as np
    spot_arr = np.asarray(spot_sek_kwh)
    mean_spot = round(float(spot_arr.mean()), 3) if len(spot_arr) > 0 else 0.85
    daily_spreads = []
    for i in range(0, len(spot_arr), 24):
        chk = spot_arr[i : i + 24]
        if len(chk) >= 12:
            daily_spreads.append(float(np.max(chk) - np.min(chk)))
    mean_spread = round(float(np.mean(daily_spreads)), 3) if daily_spreads else 0.80

    stats = {
        "mean_spot_sek_kwh": mean_spot,
        "mean_daily_spread_sek_kwh": mean_spread,
        "arbitrage_yield_sek_per_kwh": round(mean_spread * 0.88 * 230.0, 1),
        "solar_avoided_cost_sek_kwh": round(mean_spot + 0.855, 3),
        # What a household pays and is paid on average, which is what the value
        # of storing a kWh actually hangs on.
        "mean_import_price_sek_kwh": round(float(household_prices(spot_arr)[0].mean()), 3),
        "mean_export_price_sek_kwh": round(float(household_prices(spot_arr)[1].mean()), 3),
    }

    return {
        "zone": zone,
        "name": ZONE_NAMES.get(zone, zone),
        "stats": stats,
        "_hourly": hourly,
        "offers": offers_data,
        "backtest": backtest_data,
    }


def build_bess_payload(
    actuals: list[dict],
    fx_rate: float = 11.33,
    zone: str = "SE4",
    now: datetime | None = None,
) -> dict[str, Any]:
    """Compile the full BESS analysis payload covering all bidding zones."""
    now = now or now_local()

    zones_data: dict[str, Any] = {}
    for z in ("SE1", "SE2", "SE3", "SE4"):
        zones_data[z] = _build_single_zone_data(actuals, fx_rate=fx_rate, zone=z, now=now)

    default_z = zones_data.get(zone, zones_data["SE4"])

    # 3. Compile strategic advice blocks from the dossier
    advice_sv = {
        "case_house": {
            "zone": "SE4 (Skåne / Sydsverige)",
            "pv_capacity_kwp": 10.0,
            "annual_pv_kwh": 7000,
            "annual_load_kwh": 8000,
            "current_export_kwh": 5000,
            "evening_night_load_kwh": "8–11 kWh (sommar), 10–12 kWh (vinter)",
        },
        "key_reforms_2026": [
            {
                "title": "1. 60-öringen är slopad",
                "desc": "Skattereduktionen för mikroproduktion (60 öre/kWh) togs bort 1 januari 2026. Inmatad el ger nu endast spotpris plus ca 6 öre i nätnytta. Värdet av att lagra en kWh i stället för att sälja den steg därmed med 60 öre – till omkring 0,6 kr i SE1–SE2, 1,0 kr i SE3 och 1,4 kr i SE4 med det senaste årets priser.",
            },
            {
                "title": "2. Effektavgiftskravet upphävt",
                "desc": "Ei upphävde föreskrifterna i juni 2026. Ellevio återgick till säkringsabonnemang. Räkna inte med effekttoppskapning förrän nätbolaget bekräftat framtida modell (utreds till 2027).",
            },
            {
                "title": "3. Batteriavdraget ligger kvar men har tak",
                "desc": "Grön teknik ger fortsatt 50 % (effektivt 48,5 %) på batterier. Taket är dock strikt 50 000 kr per person och år. Vid bruttopris över ca 103 000 kr sprängs taket om fastigheten har en ensam ägare.",
            },
        ],
        "dimensioning": {
            "villa_recommendation": "10–13 kWh för normalvilla (8 000–15 000 kWh/år).",
            "high_load_recommendation": "20–60 kWh för storvilla, fastighet, lantbruk eller dubbla elbilar (25 000–60 000 kWh/år).",
            "rationale": "I en normalvilla är kvällsbehovet 8–11 kWh, varför batterier över 13 kWh får låg marginalnytta i ren solel. I ett hushåll med 50 000 kWh förbrukning kan dock ett enda vinterdygn dra 200–300 kWh (varav 60–120 kWh kväll/natt). Där töms ett 10 kWh batteri på under två timmar, och ett 20–60 kWh system kan cyklas djupt för både solel, nätarbitrage och effektskydd.",
            "tax_tip": "Stora system (20–60 kWh) kostar 140 000–300 000 kr brutto och spränger det individuella avdragstaket (50 000 kr). Se till att ha två ägare på lagfarten (100 000 kr tak) eller köp modulärt fördelat över två kalenderår för att nyttja upp till 200 000 kr i Grönt avdrag.",
        },
        "questions_before_signing": [
            "Hur många ägare finns på fastigheten, och hur mycket avdragsutrymme för grön teknik återstår i år?",
            "Vilken batterimodell ingår i offerten, och vem bär garantin år 11–15?",
            "Vilket C-tal har batteripaketet? Vid 0,5 ger ett 15 kWh-batteri bara 7,5 kW budbar effekt, även om växelriktaren klarar 12 kW.",
            "Begär prislista och historisk ersättning för stödtjänsterna (ingen leverantör lämnar fast garanti på detta).",
        ],
    }

    payload = {
        "generated_at": iso(now),
        "zone": zone,
        "fx_rate": fx_rate,
        "offers": default_z["offers"],
        "backtest": default_z["backtest"],
        "zones": zones_data,
        "advice_sv": advice_sv,
    }
    return payload


def hourly_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Prices and household shapes for the browser calculator, and the constants both sides share."""
    from .dispatch import (
        ENERGY_TAX_SEK, FCR_WINDOW_RESERVE, GRID_BENEFIT_EXPORT_SEK, GRID_TRANSFER_FEE_SEK,
        MODEL_VERSION, REALISATION, RETAIL_MARGIN_SEK, SPOT_VAT,
    )
    from .optimal import LEVELS, WEAR_HURDLE_SEK_PER_KWH
    from .solar import SYSTEM_FACTOR

    zones = payload.get("zones", {})
    first = next((z["_hourly"] for z in zones.values() if z.get("_hourly")), None) or {}
    first_extreme = next((z["_hourly"]["extreme"] for z in zones.values() if (z.get("_hourly") or {}).get("extreme")), None) or {}
    return {
        "generated_at": payload.get("generated_at"),
        "model": MODEL_VERSION,
        "from": first.get("from"),
        "hours": first.get("hours", 0),
        "constants": {
            "spot_vat": SPOT_VAT,
            "import_adders_sek": round(ENERGY_TAX_SEK + GRID_TRANSFER_FEE_SEK + RETAIL_MARGIN_SEK, 4),
            "export_adder_sek": GRID_BENEFIT_EXPORT_SEK,
            "realisation": REALISATION,
            "wear_hurdle_sek_per_kwh": WEAR_HURDLE_SEK_PER_KWH,
            "fcr_window_reserve": FCR_WINDOW_RESERVE,
            "levels": LEVELS,
            "case_house_pv_kwp": CASE_HOUSE_PV_KWP_EFFECTIVE,
            "pv_system_factor": SYSTEM_FACTOR,
        },
        "w_load": first.get("w_load", []),
        "spot": {zone: data["_hourly"]["spot"] for zone, data in zones.items() if data.get("_hourly")},
        "pv_per_kwp": {zone: data["_hourly"]["pv_per_kwp"] for zone, data in zones.items() if data.get("_hourly")},
        # 2022 in each zone, for the extreme year of the lifecycle.
        "extreme": {
            "year": EXTREME_YEAR,
            "w_load": first_extreme.get("w_load", []),
            "spot": {z: d["_hourly"]["extreme"]["spot"] for z, d in zones.items() if (d.get("_hourly") or {}).get("extreme")},
            "pv_per_kwp": {z: d["_hourly"]["extreme"]["pv_per_kwp"] for z, d in zones.items() if (d.get("_hourly") or {}).get("extreme")},
        },
    }


def write_bess(payload: dict[str, Any]) -> None:
    """Save bess payload to api and site directories."""
    hourly = hourly_payload(payload)
    for data in payload.get("zones", {}).values():
        data.pop("_hourly", None)
    (SITE_DATA_DIR / "bess-hourly.json").write_text(
        json.dumps(hourly, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
    )
    write_json("bess.json", payload)
    site_file = SITE_DATA_DIR / "bess.json"
    site_file.parent.mkdir(parents=True, exist_ok=True)
    site_file.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    log.info("Wrote BESS valuation to %s and API", site_file)

    try:
        from .utility import write_utility_bess
        write_utility_bess()
        log.info("Wrote utility BESS valuation to bess-utility.json")
    except Exception as exc:
        log.warning("Could not write utility BESS data: %s", exc)


"""Offers and hardware specifications for home battery tracks.

Encapsulates the six vendor offers evaluated for the SE4 case house (Bengt Ekenstierna),
their hardware limits (C-rate, continuous power, round-trip efficiency), pricing,
deduction rules, lock-in, and guarantees.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# Grön teknik avdrag: 50 % av arbetskostnad och material (schablon: 48,5 % effektivt avdrag)
GREEN_TECH_RATE = 0.485
GREEN_TECH_CAP_PER_PERSON = 50_000.0


@dataclass(frozen=True)
class BatteryOffer:
    id: str
    name: str
    vendor: str
    hardware: str
    inverter_kw: float
    capacity_kwh: float
    usable_kwh: float
    c_rate: float
    battery_max_power_kw: float
    round_trip_eff: float
    gross_price: float
    campaign_discount: float
    warranty_years: int
    warranty_cycles: int | None
    lock_in_desc: str
    lock_in_level: str  # "none", "moderate", "severe"
    islanding: str  # "no", "yes", "option"
    outdoor_placement: bool
    v2g_ready: bool
    rank: int
    notes_sv: str

    def net_price(self, num_owners: int = 2) -> float:
        """Net price after Grön teknik tax deduction (capped per calendar year)."""
        max_deduction = num_owners * GREEN_TECH_CAP_PER_PERSON
        potential_deduction = self.gross_price * GREEN_TECH_RATE
        actual_deduction = min(potential_deduction, max_deduction)
        return max(0.0, self.gross_price - actual_deduction - self.campaign_discount)

    def deduction_lost(self, num_owners: int = 1) -> float:
        """Amount of deduction lost if capped."""
        max_deduction = num_owners * GREEN_TECH_CAP_PER_PERSON
        potential_deduction = self.gross_price * GREEN_TECH_RATE
        return max(0.0, potential_deduction - max_deduction)

    def to_dict(self, num_owners: int = 2) -> dict[str, Any]:
        net = self.net_price(num_owners)
        lost = self.deduction_lost(num_owners)
        return {
            "id": self.id,
            "name": self.name,
            "vendor": self.vendor,
            "hardware": self.hardware,
            "inverter_kw": self.inverter_kw,
            "capacity_kwh": self.capacity_kwh,
            "usable_kwh": self.usable_kwh,
            "c_rate": self.c_rate,
            "battery_max_power_kw": self.battery_max_power_kw,
            "round_trip_eff": self.round_trip_eff,
            "gross_price": self.gross_price,
            "campaign_discount": self.campaign_discount,
            "net_price": round(net, 0),
            "net_price_1_owner": round(self.net_price(1), 0),
            "net_price_2_owners": round(self.net_price(2), 0),
            "deduction_lost_1_owner": round(self.deduction_lost(1), 0),
            "cost_per_kwh_net": round(net / self.capacity_kwh, 0),
            "warranty_years": self.warranty_years,
            "warranty_cycles": self.warranty_cycles,
            "lock_in_desc": self.lock_in_desc,
            "lock_in_level": self.lock_in_level,
            "islanding": self.islanding,
            "outdoor_placement": self.outdoor_placement,
            "v2g_ready": self.v2g_ready,
            "rank": self.rank,
            "notes_sv": self.notes_sv,
        }


OFFERS: list[BatteryOffer] = [
    BatteryOffer(
        id="solis_dyness_15",
        name="Solis + Dyness 15 kWh",
        vendor="Solis / Dyness",
        hardware="Solis S6 hybrid 10 kW + Dyness Stack 100 (15 kWh)",
        inverter_kw=10.0,
        capacity_kwh=15.0,
        usable_kwh=13.5,
        c_rate=0.67,
        battery_max_power_kw=10.0,
        round_trip_eff=0.90,
        gross_price=53_500.0,
        campaign_discount=0.0,
        warranty_years=10,
        warranty_cycles=6000,
        lock_in_desc="Fritt aggregatorval (CheckWatt, Flower, Tibber m.fl.). Ingen inlåsning.",
        lock_in_level="none",
        islanding="option",  # 13 000 kr extra
        outdoor_placement=False,
        v2g_ready=False,
        rank=1,
        notes_sv="Lägst kapitalkostnad (1 835 kr/kWh netto). Befintlig växelriktare byts. 10 års garanti.",
    ),
    BatteryOffer(
        id="sigenergy_18",
        name="Sigenergy 18 kWh",
        vendor="Sigenergy",
        hardware="SigenStor 12 kW hybrid + 18 kWh batteri (allt-i-ett)",
        inverter_kw=12.0,
        capacity_kwh=18.0,
        usable_kwh=16.2,
        c_rate=0.67,
        battery_max_power_kw=12.0,
        round_trip_eff=0.91,
        gross_price=70_000.0,
        campaign_discount=0.0,
        warranty_years=10,
        warranty_cycles=None,  # Cap at ~3 MWh throughput per kWh
        lock_in_desc="CheckWatt & Flower stöds. Inte kompatibel med Tibber Bridge.",
        lock_in_level="moderate",
        islanding="option",  # 20 000 kr extra
        outdoor_placement=True,
        v2g_ready=True,  # DC-laddare tillval 28 000 kr
        rank=2,
        notes_sv="Bäst teknisk funktion och IP66 utomhusplacering. 10 års garanti och throughput-tak.",
    ),
    BatteryOffer(
        id="svea_solar_10",
        name="Svea Solar 10 kWh",
        vendor="Svea Solar",
        hardware="10 kW hybrid + 10 kWh batterimodul",
        inverter_kw=10.0,
        capacity_kwh=10.0,
        usable_kwh=9.0,
        c_rate=0.75,
        battery_max_power_kw=7.5,
        round_trip_eff=0.89,
        gross_price=87_000.0,
        campaign_discount=10_000.0,  # Kampanjrabatt / fri el
        warranty_years=15,
        warranty_cycles=6000,
        lock_in_desc="Sunbeam aggregator kräver Svea Solars eget elhandelsavtal.",
        lock_in_level="severe",
        islanding="option",
        outdoor_placement=True,
        v2g_ready=False,
        rank=3,
        notes_sv="Rätt fysisk storlek (10 kWh). Stark inlåsning av elavtalet. 15 års garanti.",
    ),
    BatteryOffer(
        id="greenely_polarium_13_8",
        name="Greenely / Polarium 13,8 kWh",
        vendor="Greenely",
        hardware="Polarium HomeBattery 2 (13,8 kWh AC-kopplat)",
        inverter_kw=10.0,
        capacity_kwh=13.8,
        usable_kwh=12.4,
        c_rate=0.55,
        battery_max_power_kw=7.5,
        round_trip_eff=0.88,
        gross_price=132_000.0,
        campaign_discount=30_000.0,  # Kampanjbonus
        warranty_years=15,
        warranty_cycles=6000,
        lock_in_desc="Greenely VPP med 36 månaders bindning (återbetalning 30/20/10 tkr vid förtida avslut) och Greenely elhandel.",
        lock_in_level="severe",
        islanding="no",
        outdoor_placement=True,
        v2g_ready=False,
        rank=4,
        notes_sv="AC-lösning mot befintlig växelriktare. Spränger avdragstaket vid 1 ägare. 36 mån bindning.",
    ),
    BatteryOffer(
        id="elteknik_saj_15",
        name="Elteknik i Lund (SAJ HS3 15 kWh)",
        vendor="Elteknik i Lund",
        hardware="SAJ HS3 12 kW hybrid + 3x 5 kWh batterimoduler",
        inverter_kw=12.0,
        capacity_kwh=15.0,
        usable_kwh=13.5,
        c_rate=0.50,  # 0.5C limit confirmed!
        battery_max_power_kw=7.5,
        round_trip_eff=0.89,
        gross_price=113_200.0,
        campaign_discount=0.0,
        warranty_years=10,
        warranty_cycles=None,
        lock_in_desc="Tibber Bridge ingår, men stödtjänster kräver byte till Tibber elhandel.",
        lock_in_level="moderate",
        islanding="yes",  # Integrerad
        outdoor_placement=True,
        v2g_ready=False,
        rank=5,
        notes_sv="C-tal 0,5 begränsar biddbar effekt till 7,5 kW av 12 kW växelriktare. 10 års fabriksgaranti.",
    ),
    BatteryOffer(
        id="evify_saj_15",
        name="Evify (SAJ HS3 15 kWh)",
        vendor="Evify Sweden",
        hardware="SAJ HS3 12 kW hybrid + 3x 5 kWh batterimoduler",
        inverter_kw=12.0,
        capacity_kwh=15.0,
        usable_kwh=13.5,
        c_rate=0.50,
        battery_max_power_kw=7.5,
        round_trip_eff=0.89,
        gross_price=114_395.0,
        campaign_discount=0.0,
        warranty_years=15,  # Partner extended
        warranty_cycles=6000,
        lock_in_desc="elekeeper ingår fritt, men stödtjänster kräver byte till Tibber elhandel.",
        lock_in_level="moderate",
        islanding="yes",
        outdoor_placement=True,
        v2g_ready=False,
        rank=6,
        notes_sv="Samma hårdvara som Elteknik men 15 års partnergaranti. 6 692 kr/kWh brutto.",
    ),
]


def get_offer(offer_id: str) -> BatteryOffer:
    for o in OFFERS:
        if o.id == offer_id:
            return o
    raise KeyError(f"Unknown offer ID: {offer_id}")

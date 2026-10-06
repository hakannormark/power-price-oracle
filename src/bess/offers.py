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
        name="Typfall A · 15 kWh, fritt aggregatorval",
        vendor="Typfall",
        hardware="Hybridväxelriktare 10 kW + 15 kWh batteri",
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
        lock_in_desc="Fritt val av aggregator och elhandlare. Ingen inlåsning.",
        lock_in_level="none",
        islanding="option",  # 13 000 kr extra
        outdoor_placement=False,
        v2g_ready=False,
        rank=1,
        notes_sv="Lägst kapitalkostnad per kWh. Befintlig växelriktare byts. 10 års garanti.",
    ),
    BatteryOffer(
        id="sigenergy_18",
        name="Typfall B · 18 kWh, allt-i-ett utomhus",
        vendor="Typfall",
        hardware="Allt-i-ett-system 12 kW + 18 kWh batteri",
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
        lock_in_desc="Flera aggregatorer stöds, men inte alla styrsystem.",
        lock_in_level="moderate",
        islanding="option",  # 20 000 kr extra
        outdoor_placement=True,
        v2g_ready=True,  # DC-laddare tillval 28 000 kr
        rank=2,
        notes_sv="Utomhusplacering (IP66). 10 års garanti med tak för total energigenomströmning.",
    ),
    BatteryOffer(
        id="svea_solar_10",
        name="Typfall C · 10 kWh, bundet elavtal",
        vendor="Typfall",
        hardware="Hybridväxelriktare 10 kW + 10 kWh batteri",
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
        lock_in_desc="Leverantörens egen aggregator, som kräver leverantörens elhandelsavtal.",
        lock_in_level="severe",
        islanding="option",
        outdoor_placement=True,
        v2g_ready=False,
        rank=3,
        notes_sv="Rätt storlek för en normalvilla. Stark inlåsning av elavtalet. 15 års garanti.",
    ),
    BatteryOffer(
        id="greenely_polarium_13_8",
        name="Typfall D · 13,8 kWh, AC-kopplat med bindningstid",
        vendor="Typfall",
        hardware="AC-kopplat batteri 13,8 kWh mot befintlig växelriktare",
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
        lock_in_desc="Leverantörens virtuella kraftverk med 36 månaders bindning, återbetalning vid förtida avslut och leverantörens elhandel.",
        lock_in_level="severe",
        islanding="no",
        outdoor_placement=True,
        v2g_ready=False,
        rank=4,
        notes_sv="AC-lösning mot befintlig växelriktare. Spränger avdragstaket vid en ägare. 36 månaders bindning.",
    ),
    BatteryOffer(
        id="elteknik_saj_15",
        name="Typfall E · 15 kWh, lågt C-tal, 10 års garanti",
        vendor="Typfall",
        hardware="Hybridväxelriktare 12 kW + 3 × 5 kWh batterimoduler",
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
        lock_in_desc="Styrsystem ingår, men stödtjänster kräver byte till en viss elhandlare.",
        lock_in_level="moderate",
        islanding="yes",  # Integrerad
        outdoor_placement=True,
        v2g_ready=False,
        rank=5,
        notes_sv="C-tal 0,5 begränsar budbar effekt till 7,5 kW av växelriktarens 12 kW. 10 års fabriksgaranti.",
    ),
    BatteryOffer(
        id="evify_saj_15",
        name="Typfall F · 15 kWh, lågt C-tal, 15 års garanti",
        vendor="Typfall",
        hardware="Hybridväxelriktare 12 kW + 3 × 5 kWh batterimoduler",
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
        lock_in_desc="Styrsystem ingår, men stödtjänster kräver byte till en viss elhandlare.",
        lock_in_level="moderate",
        islanding="yes",
        outdoor_placement=True,
        v2g_ready=False,
        rank=6,
        notes_sv="Samma hårdvara som typfall E men 15 års garanti via installatören.",
    ),
]


def get_offer(offer_id: str) -> BatteryOffer:
    for o in OFFERS:
        if o.id == offer_id:
            return o
    raise KeyError(f"Unknown offer ID: {offer_id}")

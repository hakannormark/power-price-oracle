"""Independent checks of what the site publishes.

    python -m src.verify            # run the local checks, write site/data/verification.json
    python -m src.verify --prices   # also compare recent prices with an independent source (network)

Every check here recomputes a published number by another route than the code
that produced it: another source, another algorithm, or plain arithmetic on the
published parts. A check that fails does not stop the pipeline; it is shown on
the control page (kontroll.html) for anyone to see, and tests/test_verify.py
fails so that a change that breaks one cannot be merged unnoticed.

What this cannot check is whether an assumption is right: the pay for ancillary
services a user enters, that the price pattern of the last twelve months lasts,
the performance ratio of a solar installation. Those are listed on the control
page as assumptions, not as verified.
"""

from __future__ import annotations

import json
import logging
import math
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable

import numpy as np

from .config import DATA_DIR, EVAL_WINDOW_DAYS, HORIZON_HOURS, SITE_DATA_DIR, ZONES
from .timeutil import iso, now_local

log = logging.getLogger(__name__)

PRICE_RECORD = DATA_DIR / "verification" / "prices.json"
OUT = SITE_DATA_DIR / "verification.json"

# PVGIS 5.2 (EU Joint Research Centre), kWh per kWp and year, 35 degrees tilt,
# 14 % system loss, at the four zone points. Fetched 2026-10-07.
PVGIS = {
    "south": {"SE1": 975, "SE2": 903, "SE3": 987, "SE4": 1053},
    "eastwest": {"SE1": 730, "SE2": 682, "SE3": 756, "SE4": 815},
}


def _read(name: str) -> Any:
    return json.loads((SITE_DATA_DIR / name).read_text(encoding="utf-8"))


def _n(value: float) -> str:
    """Whole number with a thin space between thousands."""
    return f"{value:,.0f}".replace(",", "\u202f")


def _hour(ts: str) -> int:
    return int(datetime.fromisoformat(ts).timestamp()) // 3600


# ------------------------------------------------------------------ prices
def check_price_history(actuals: list[dict]) -> dict:
    """Every hour since the first is there, once, for every zone."""
    hours: dict[str, set[int]] = defaultdict(set)
    rows = 0
    for r in actuals:
        hours[r["zone"]].add(_hour(r["ts"]))
        rows += 1
    missing = {z: (max(h) - min(h) + 1) - len(h) for z, h in hours.items()}
    duplicates = rows - sum(len(h) for h in hours.values())
    first = min(min(h) for h in hours.values())
    ok = set(hours) == set(ZONES) and not any(missing.values()) and duplicates == 0
    return {
        "ok": ok,
        "detail": f"{_n(sum(len(h) for h in hours.values()))} timpriser sedan "
                  f"{datetime.fromtimestamp(first * 3600, tz=timezone.utc).date()}; saknade timmar: {sum(missing.values())}, dubbletter: {duplicates}.",
    }


def compare_prices_with_source(actuals: list[dict], days: int = 45) -> dict:  # pragma: no cover - network
    """Stored prices against energy-charts.info for the last `days` days. Writes the record."""
    import time

    from .fetch.energy_charts import fetch_prices

    end = datetime.now(timezone.utc).date() - timedelta(days=1)
    start = end - timedelta(days=days)
    have = {(r["zone"], r["ts"]): r["price_eur_mwh"] for r in actuals if r["ts"][:10] >= start.isoformat()}
    compared = differing = 0
    worst = 0.0
    for zone in ZONES:
        for row in fetch_prices(zone, start, end):
            ours = have.get((zone, row["ts"]))
            if ours is None:
                continue
            compared += 1
            diff = abs(ours - row["price_eur_mwh"])
            worst = max(worst, diff)
            differing += diff > 0.011
        time.sleep(20)
    record = json.loads(PRICE_RECORD.read_text(encoding="utf-8")) if PRICE_RECORD.exists() else {}
    record["latest"] = {
        "checked_at": iso(now_local()), "from": start.isoformat(), "to": end.isoformat(),
        "hours_compared": compared, "hours_differing": int(differing), "largest_difference_eur_mwh": round(worst, 3),
    }
    PRICE_RECORD.parent.mkdir(parents=True, exist_ok=True)
    PRICE_RECORD.write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
    return record


def check_price_source() -> dict:
    """What the comparisons with the independent source found, and when."""
    if not PRICE_RECORD.exists():
        return {"ok": False, "detail": "Ingen jämförelse med oberoende källa är gjord."}
    record = json.loads(PRICE_RECORD.read_text(encoding="utf-8"))
    full, latest = record.get("full_history") or {}, record.get("latest") or {}
    ok = bool(full) and full.get("hours_differing_after_correction", 1) == 0 and (not latest or latest.get("hours_differing", 1) == 0)
    parts = []
    if full:
        parts.append(f"Hela historiken jämförd {full['checked_at'][:10]}: {_n(full['hours_compared'])} timmar, "
                     f"{full['hours_differing_after_correction']} avviker (före rättning: {full['hours_differing_found']}, alla i ett dygn i SE2 2022)")
    if latest:
        parts.append(f"Senaste löpande kontroll {latest['checked_at'][:10]}: {_n(latest['hours_compared'])} timmar {latest['from']}–{latest['to']}, "
                     f"{latest['hours_differing']} avviker")
    return {"ok": ok, "detail": ". ".join(parts) + "."}


def check_site_prices(actuals: list[dict]) -> dict:
    """The prices on the zone pages are the stored ones, and öre is EUR times the stated rate."""
    stored = {(r["zone"], _hour(r["ts"])): r["price_eur_mwh"] for r in actuals[-80000:]}
    compared = bad = 0
    for zone in ZONES:
        page = _read(f"{zone.lower()}.json")
        rate = page["fx"]["rate"]
        for row in page["next_hours"]:
            if row.get("source") != "official":
                continue
            ours = stored.get((zone, _hour(row["ts"])))
            if ours is None:
                continue
            compared += 1
            bad += abs(ours - row["eur_mwh"]) > 0.01 or abs(row["eur_mwh"] * rate / 10.0 - row["ore_kwh"]) > 0.05
        for row in page["series"]:
            ours = stored.get((zone, _hour(row["ts"])))
            if ours is not None and row.get("actual") is not None:
                compared += 1
                bad += abs(ours - row["actual"]) > 0.01
    return {"ok": compared > 0 and bad == 0, "detail": f"{compared} visade timpriser jämförda med lagrade priser och växelkursen; {bad} avviker."}


def check_forecast_bands() -> dict:
    """Forecast hours are in order, an hour apart, and the band contains the forecast."""
    hours = bad = 0
    for zone in ZONES:
        rows = _read(f"{zone.lower()}.json")["next_hours"]
        stamps = [_hour(r["ts"]) for r in rows]
        bad += sum(1 for a, b in zip(stamps, stamps[1:]) if b - a != 1)
        for r in rows:
            hours += 1
            if r.get("source") == "official":
                continue
            p10, p50, p90 = r.get("p10"), r.get("eur_mwh"), r.get("p90")
            bad += p10 is None or p90 is None or not (p10 <= p50 + 1e-6 <= p90 + 2e-6) or not math.isfinite(p50)
    return {"ok": hours > 0 and bad == 0, "detail": f"{hours} prognostimmar i fyra elområden: obruten tidsaxel och p10 ≤ prognos ≤ p90 överallt; {bad} fel."}


# --------------------------------------------------------------- accuracy
def check_accuracy(forecasts: list[dict], actuals: list[dict], now: datetime | None = None) -> dict:
    """The mean error of the default model, recomputed from the logs without the scoring module."""
    from .timeutil import auction_publication_time
    from .models.registry import MODEL_DEFINED_SINCE
    from .schedule import slot_start

    published = _read("accuracy.json")
    model = published["default_model"]
    now = datetime.fromisoformat(published["generated_at"])
    cutoff = now - timedelta(days=EVAL_WINDOW_DAYS)
    since = MODEL_DEFINED_SINCE.get(model)
    since_dt = datetime.fromisoformat(since) if since else None
    actual = {(r["zone"], _hour(r["ts"])): r["price_eur_mwh"] for r in actuals if r["ts"][:4] >= str(cutoff.year)}

    # The first issue in each schedule slot is the one that counts.
    first: dict[datetime, datetime] = {}
    rows = []
    for f in forecasts:
        if f["model_id"] != model or not (0 <= f["horizon_h"] < HORIZON_HOURS) or f.get("p50") is None:
            continue
        issued = datetime.fromisoformat(f["issued_at"])
        if since_dt and issued < since_dt:
            continue
        slot = slot_start(issued)
        if slot not in first or issued < first[slot]:
            first[slot] = issued
        rows.append((slot, issued, f))
    errors: dict[str, list[float]] = defaultdict(list)
    for slot, issued, f in rows:
        if issued != first[slot]:
            continue
        target = datetime.fromisoformat(f["ts"])
        if target < cutoff or issued >= auction_publication_time(target):
            continue
        outcome = actual.get((f["zone"], _hour(f["ts"])))
        if outcome is None:
            continue
        lo = int(f["horizon_h"]) // 24 * 24
        errors[f"{lo}-{lo + 24}h"].append(f["p50"] - outcome)

    compared, worst = 0, 0.0
    lines = []
    for bucket, stats in published["overall"][model].items():
        mine = errors.get(bucket) or []
        if not mine:
            return {"ok": False, "detail": f"Inga egna poängsatta prognoser för {bucket}."}
        mae = sum(abs(e) for e in mine) / len(mine)
        compared += 1
        worst = max(worst, abs(mae - stats["mae"]))
        if len(mine) != stats["n"]:
            return {"ok": False, "detail": f"{bucket}: {len(mine)} poängsatta timmar här mot {stats['n']} publicerade."}
        lines.append(f"{bucket}: {mae:.2f}")
    return {
        "ok": compared > 0 and worst < 0.01,
        "detail": f"Standardmodellens medelfel ({model}) omräknat ur prognosloggen och utfallen, per horisont i EUR/MWh: "
                  + ", ".join(lines) + f". Största skillnad mot träffsäkerhetssidan: {worst:.3f}.",
    }


# ------------------------------------------------------------ home battery
def _lp_bill(net: np.ndarray, imp: np.ndarray, exp: np.ndarray, window: float, power: float, rte: float, hurdle: float) -> float:
    """The cheapest bill by linear programming: a different method from the dynamic programme."""
    from scipy.optimize import linprog
    from scipy.sparse import lil_matrix

    n = len(net)
    eta = math.sqrt(rte)
    # Variables per hour: charge, discharge, import, export, stored.
    c0, d0, i0, e0, s0 = 0, n, 2 * n, 3 * n, 4 * n
    cost = np.zeros(5 * n)
    cost[d0:d0 + n] = hurdle
    cost[i0:i0 + n] = imp
    cost[e0:e0 + n] = -exp
    eq = lil_matrix((2 * n, 5 * n))
    rhs = np.zeros(2 * n)
    for t in range(n):
        eq[t, i0 + t], eq[t, e0 + t], eq[t, c0 + t], eq[t, d0 + t] = 1, -1, -1, 1   # import - export = net + charge - discharge
        rhs[t] = net[t]
        eq[n + t, s0 + t], eq[n + t, c0 + t], eq[n + t, d0 + t] = 1, -eta, 1 / eta   # stored follows the flows
        if t:
            eq[n + t, s0 + t - 1] = -1
    bounds = [(0, power)] * (2 * n) + [(0, None)] * (2 * n) + [(0, window)] * n
    res = linprog(cost, A_eq=eq.tocsr(), b_eq=rhs, bounds=bounds, method="highs")
    if not res.success:
        raise RuntimeError(res.message)
    return float(res.fun)


def check_battery_optimiser() -> dict:
    """The battery's schedule against a linear programme on four weeks per zone."""
    from .bess.optimal import WEAR_HURDLE_SEK_PER_KWH, bill_without_battery, optimal_schedule

    hourly = _read("bess-hourly.json")
    k = hourly["constants"]
    worst, lines = 1.0, []
    for zone in ZONES:
        n = 24 * 28
        start = (len(hourly["spot"][zone]) // 2 // 24) * 24      # four weeks in the middle of the year
        spot = np.array(hourly["spot"][zone][start:start + n])
        base = np.array(hourly["base"] if isinstance(hourly["base"], list) else hourly["base"][zone], dtype=float)[start:start + n]
        deg = np.array(hourly["deg"][zone][start:start + n]) / 10.0
        raw = (1 - k["heating_share"]) * base / base.mean() + k["heating_share"] * deg / k["reference_mean_degrees"]
        load = raw * (8000.0 * n / 8760.0) / raw.sum()
        pv = np.array(hourly["pv"]["south"][zone][start:start + n]) / 1000.0 * 10.0
        imp, exp = spot * k["spot_vat"] + k["import_adders_sek"], spot + k["export_adder_sek"]
        without = bill_without_battery(load, pv, imp, exp)
        sched = optimal_schedule(load, pv, imp, exp, 13.5, 10.0, 0.9)
        dp = sched.cost + WEAR_HURDLE_SEK_PER_KWH * float(sched.discharged.sum())
        lp = _lp_bill(load - pv, imp, exp, 13.5, 10.0, 0.9, WEAR_HURDLE_SEK_PER_KWH)
        ratio = (without - dp) / (without - lp) if without > lp else 1.0
        worst = min(worst, ratio)
        if dp < lp - 0.5:
            return {"ok": False, "detail": f"{zone}: schemat är billigare än det teoretiskt bästa, vilket inte kan stämma."}
        lines.append(f"{zone} {ratio * 100:.1f} %")
    return {
        "ok": worst > 0.97,
        "detail": "Batteriets körschema (dynamisk programmering, 49 nivåer) mot en linjär optimering av samma fyra veckor, "
                  "andel av den teoretiskt bästa besparingen: " + ", ".join(lines) + ".",
    }


def check_lifecycle() -> dict:
    """Payback and net present value recomputed from the published cash flows."""
    bess = _read("bess.json")
    compared = bad = 0
    for zone, data in bess["zones"].items():
        for offer in data["offers"]:
            for key, lc in offer["lifecycle"].items():
                owners = 1 if key.endswith("_1_owner") else 2
                net_price = offer[f"net_price_{owners}_owner{'s' if owners == 2 else ''}"]
                cumulative, npv, payback = -net_price, -net_price, None
                for cf in lc["cash_flows"]:
                    parts = cf["solar_savings"] + cf["arbitrage_profit"] + cf["ancillary_revenue"]
                    bad += abs(parts - cf["net_cash_flow"]) > 2.0
                    previous = cumulative
                    cumulative += cf["net_cash_flow"]
                    npv += cf["net_cash_flow"] / 1.05 ** cf["year"]
                    if payback is None and previous < 0 <= cumulative:
                        payback = cf["year"] - 1 + (-previous) / cf["net_cash_flow"]
                compared += 1
                bad += abs(npv - lc["npv_15y"]) > 15.0
                bad += (payback is None) != (lc["payback_years"] is None) or (payback is not None and abs(payback - lc["payback_years"]) > 0.06)
    return {"ok": compared > 0 and bad == 0, "detail": f"{compared} kalkyler (elområde × typfall × scenario × ägare): årens delar summerar till kassaflödet, "
                                                         f"och återbetalningstid och nuvärde vid 5 % ränta stämmer med de publicerade; {bad} avviker."}


def check_solar() -> dict:
    """The sun against PVGIS, an independent data set and method."""
    from .bess import solar

    worst, lines = 0.0, []
    for orientation, reference in PVGIS.items():
        for zone, ref in reference.items():
            years = []
            for year in range(2015, 2026):
                stamps = [datetime(year, 1, 1, tzinfo=timezone.utc) + timedelta(hours=i) for i in range(8760)]
                series = solar.production_per_kwp(zone, stamps, orientation)
                if series is None:
                    return {"ok": False, "detail": "Instrålningsdata saknas."}
                years.append(float(series.sum()))
            ours = sum(years) / len(years)
            worst = max(worst, abs(ours / ref - 1))
            if orientation == "south":
                lines.append(f"{zone} {ours:.0f} mot {ref}")
    return {"ok": worst < 0.10, "detail": "Solproduktion per kWp och år, medel 2015–2025, mot EU:s PVGIS (söder 35°): " + ", ".join(lines)
                                          + f". Största avvikelse inklusive öst–väst: {worst * 100:.0f} %."}


# -------------------------------------------------------------- BESS Studio
def check_studio_arbitrage(actuals: list[dict]) -> dict:
    """BESS Studio's arbitrage (a weekly linear programme) against the home battery's dynamic programme."""
    from .bess.optimal import optimal_schedule

    published = _read("bess-map/dispatch_backtest.json")
    a = published["assumptions"]
    lines, worst = [], 0.0
    for zone in ("SE1", "SE4"):
        year = "2025"
        spot = np.array([r["price_eur_mwh"] for r in sorted(
            (r for r in actuals if r["zone"] == zone and datetime.fromisoformat(r["ts"]).year == int(year)), key=lambda r: _hour(r["ts"]))])
        dur = 2.0
        window = (a["soc_max"] - a["soc_min"]) * dur
        # One megawatt buying and selling at spot: load 0, the same price both ways.
        zero = np.zeros(len(spot))
        import src.bess.optimal as opt
        hurdle, opt.WEAR_HURDLE_SEK_PER_KWH = opt.WEAR_HURDLE_SEK_PER_KWH, a["wear_hurdle_eur_mwh"]
        try:
            sched = optimal_schedule(zero, zero, spot, spot, window, 1.0, a["rte"], levels=121)
        finally:
            opt.WEAR_HURDLE_SEK_PER_KWH = hurdle
        mine = -sched.cost * 8760.0 / len(spot)
        theirs = published["spot_years"][zone]["2"][year]["rev"]
        worst = max(worst, abs(mine / theirs - 1))
        lines.append(f"{zone} {_n(mine)} mot {_n(theirs)} EUR/MW")
    return {"ok": worst < 0.06, "detail": f"Arbitrageintäkt 2025 för 2 timmars lager, omräknad med en annan algoritm: " + ", ".join(lines)
                                          + f". Skillnad som mest {worst * 100:.1f} % (veckoblock och nivåindelning förklarar den)."}


# ------------------------------------------------------------------- runner
CHECKS: list[tuple[str, str, str]] = [
    ("price_history", "Prishistoriken är komplett", "Varje timme sedan 2015 finns en gång per elområde."),
    ("price_source", "Priserna stämmer med en oberoende källa", "Lagrade timpriser jämförs med energy-charts.info (Fraunhofer ISE), som hämtar från ENTSO-E på egen väg."),
    ("site_prices", "Sidorna visar de lagrade priserna", "Priser på elområdessidorna jämförs med lagret, och öre/kWh med EUR/MWh gånger ECB-kursen."),
    ("forecast_bands", "Prognosen är välformad", "Obruten tidsaxel och osäkerhetsband som innesluter prognosen."),
    ("accuracy", "Träffsäkerheten är rätt räknad", "Standardmodellens medelfel räknas om direkt ur prognosloggen och utfallen, utan poängsättningskoden."),
    ("battery_optimiser", "Hembatteriets körschema är nära det bästa möjliga", "Jämförs med en linjär optimering – en annan metod för samma problem."),
    ("lifecycle", "Investeringskalkylen går ihop", "Återbetalningstid och nuvärde räknas om ur de publicerade kassaflödena."),
    ("solar", "Solproduktionen stämmer med PVGIS", "Uppmätt instrålning (ERA5) gånger 0,80 jämförs med EU-kommissionens solkalkylator."),
    ("studio_arbitrage", "BESS Studios arbitrage stämmer med en annan algoritm", "Veckovis linjär optimering jämförs med dynamisk programmering över hela året."),
]

ASSUMPTIONS = [
    "Hembatteriet: att de senaste tolv månadernas prisbild består i 15 år. Åren 2015–2020 gav ungefär en tredjedel så mycket i söder.",
    "Hembatteriet: ersättningen för stödtjänster anges av användaren och är inte uppmätt. Marknadspriset på FCR-D har fallit med tre fjärdedelar på två år.",
    "Hembatteriet: 90 % av det teoretiskt bästa körschemat uppnås, 40 % av förbrukningen är uppvärmning i Malmöklimat, solanläggningen ger 80 % av instrålningen i panelens plan.",
    "BESS Studio: i ett positivt extremår följer reservpriserna spotpriset enligt ett samband uppmätt mellan dagar sedan 2024. Att det gäller ett helt år är ett antagande.",
    "BESS Studio: aktiveringsenergi, egen prispåverkan, intradag, prognosfel och otillgänglighet är inte modellerade. Resultatet är en övre gräns före dessa.",
    "Prisprognosen: osäkerhetsbandet är kalibrerat på historiska fel och täcker inte händelser utan motstycke.",
    "Långtidsprognosen: har ännu ingen skarp månad att mätas mot.",
]


def run(actuals: list[dict] | None = None, forecasts: list[dict] | None = None) -> dict:
    from .store import load_actuals, load_forecasts

    actuals = actuals if actuals is not None else load_actuals()
    functions: dict[str, Callable[[], dict]] = {
        "price_history": lambda: check_price_history(actuals),
        "price_source": check_price_source,
        "site_prices": lambda: check_site_prices(actuals),
        "forecast_bands": check_forecast_bands,
        "accuracy": lambda: check_accuracy(forecasts if forecasts is not None else load_forecasts(), actuals),
        "battery_optimiser": check_battery_optimiser,
        "lifecycle": check_lifecycle,
        "solar": check_solar,
        "studio_arbitrage": lambda: check_studio_arbitrage(actuals),
    }
    results = []
    for key, title, method in CHECKS:
        try:
            outcome = functions[key]()
        except Exception as exc:  # a check that cannot run has not passed
            log.exception("verify %s", key)
            outcome = {"ok": False, "detail": f"Kontrollen kunde inte köras: {str(exc)[:160]}"}
        results.append({"id": key, "title": title, "method": method, **outcome})
    return {
        "generated_at": iso(now_local()),
        "passed": sum(1 for r in results if r["ok"]),
        "total": len(results),
        "checks": results,
        "assumptions": ASSUMPTIONS,
    }


def write(report: dict) -> None:
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")


def main(argv: list[str]) -> int:  # pragma: no cover
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    if "--prices" in argv:
        from .store import load_actuals

        print(compare_prices_with_source(load_actuals())["latest"])
    report = run()
    write(report)
    for check in report["checks"]:
        print("OK  " if check["ok"] else "FEL ", check["title"], "—", check["detail"])
    return 0 if report["passed"] == report["total"] else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main(sys.argv))

"""Long-term forecast: build the published document, log it, score it.

Writes api/v1/longterm.json and site/data/longterm.json. One forecast per day
enters data/longterm/forecasts.jsonl — the same append-only rule as the
hourly log — and is scored once its target month has ended.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime

import pandas as pd

from ..config import CURRENCY, LONGTERM_FORECASTS_PATH, LONGTERM_HORIZON_MONTHS, SITE_DATA_DIR, ZONES
from ..explain.drivers import sv_num
from ..fetch.euronext_futures import implied_month_price, latest_snapshot
from ..store import append_jsonl, r3, read_jsonl
from ..timeutil import iso, now_local, to_local
from .backtest import run
from .models import BACKTESTED, DESCRIPTIONS_SV, FUNDAMENTAL, MARKET, PERSISTENCE, number

log = logging.getLogger(__name__)

MONTHS_SV = [
    "januari", "februari", "mars", "april", "maj", "juni",
    "juli", "augusti", "september", "oktober", "november", "december",
]

SOURCES_SV = {
    "futures": "Euronext Nord Pool Power Futures, avräkningspris (fördröjd data)",
    "fuels": "Yahoo Finance: TTF=F (gas) och CO2.L (utsläppsrätt)",
    "outlook": "ECMWF SEAS5 via Open-Meteo",
    "reservoirs": "ENTSO-E, vattenmagasinens fyllnad",
    "outages": "Nord Pool, REMIT-meddelanden om kärnkraft",
}


def month_label(period: pd.Period) -> str:
    return f"{MONTHS_SV[period.month - 1]} {period.year}"


def describe_models(default: str) -> list[dict]:
    return [
        {
            "id": model,
            "name_sv": DESCRIPTIONS_SV[model][0],
            "description_sv": DESCRIPTIONS_SV[model][1],
            "is_default": model == default,
            "is_reference": model == PERSISTENCE,
            "backtested": model in BACKTESTED,
        }
        for model in (*BACKTESTED, MARKET)
    ]


def _drivers_sv(zone: str, first: dict, months: list[dict], default: str) -> list[str]:
    """Plain Swedish on what the forecast rests on. Every figure is in the payload."""
    lines: list[str] = []
    recent, year = number(first.get("recent")), number(first.get("year_mean"))
    if recent is not None:
        tail = f", mot {sv_num(year, 1)} det senaste året." if year is not None else "."
        lines.append(f"De senaste 30 dagarna kostade elen i {zone} i snitt {sv_num(recent, 1)} EUR/MWh{tail}")

    if months and MARKET in months[0]["models"] and default in months[0]["models"]:
        market = months[0]["models"][MARKET]["p50"]
        ours = months[0]["models"][default]["p50"]
        diff = market - ours
        side = "över" if diff > 0 else "under"
        lines.append(
            f"Terminsmarknaden prissätter {months[0]['label']} till {sv_num(market, 1)} EUR/MWh, "
            f"{sv_num(abs(diff), 1)} {side} vår prognos. Terminspriset väger in allt marknaden "
            "känner till, också de planerade kärnkraftsstoppen nedan; vår modell utgår i huvudsak "
            "från prisnivån hittills."
        )

    hydro = number(first.get("hydro_anomaly"))
    if hydro is not None:
        side = "under" if hydro < 0 else "över"
        lines.append(
            f"Vattenmagasinen i hela Sverige ligger {sv_num(abs(hydro) * 100, 0)} % {side} det normala "
            "för årstiden. Mindre vatten brukar betyda högre pris framåt, eftersom vattenkraften då "
            "sparas."
        )

    if months:
        parts = [f"{sv_num(m['nuclear_out_mw'], 0)} MW i {m['label'].split()[0]}" for m in months]
        joined = parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " och " + parts[-1]
        recent_mw = number(first.get("nuclear_recent_mw")) or 0.0
        lines.append(
            f"Planerade kärnkraftsstopp tar i snitt bort {joined}, "
            f"mot {sv_num(recent_mw, 0)} MW de senaste 30 dagarna."
        )

    ttf, eua, cost = number(first.get("ttf")), number(first.get("eua")), number(first.get("gas_cost"))
    if cost is not None:
        lines.append(
            f"Gas (TTF) kostar {sv_num(ttf, 1)} EUR/MWh och en utsläppsrätt {sv_num(eua, 1)} EUR/ton. "
            f"Ett gaskraftverk behöver då ungefär {sv_num(cost, 0)} EUR/MWh för att gå runt — den nivå "
            "SE3 och SE4 dras mot när ledningarna söderut är öppna."
        )

    outlook = [m["outlook"] for m in months if m.get("outlook")]
    if outlook:
        parts = [
            f"{MONTHS_SV[int(w['month'][5:7]) - 1]} {sv_num(w['temp_anomaly_c'], 1, sign=True)} °C "
            f"och {sv_num(w['precip_anomaly_mm'], 0, sign=True)} mm nederbörd"
            for w in outlook
            if w.get("temp_anomaly_c") is not None and w.get("precip_anomaly_mm") is not None
        ]
        if parts:
            lines.append(
                f"Säsongsprognosen för vädret i {zone}, jämfört med det normala: " + "; ".join(parts)
                + ". Den visas som bakgrund och ingår inte i modellen."
            )
    return lines


def score(rows: list[dict], monthly: pd.DataFrame) -> dict:
    """MAE of logged forecasts per model and horizon, for months that have ended."""
    sums: dict[str, dict[str, list[float]]] = {}
    for row in rows:
        period = pd.Period(row["month"], freq="M")
        if row.get("p50") is None or period not in monthly.index or row["zone"] not in monthly:
            continue
        actual = monthly.at[period, row["zone"]]
        if pd.isna(actual):
            continue
        cell = sums.setdefault(row["model_id"], {}).setdefault(str(row["horizon"]), [0.0, 0])
        cell[0] += abs(row["p50"] - float(actual))
        cell[1] += 1
    models = {
        model: {h: {"mae": round(total / n, 2), "n": n} for h, (total, n) in cells.items()}
        for model, cells in sums.items()
    }
    issues = sorted({r["issue_date"] for r in rows})
    months = sorted({r["month"] for r in rows})
    return {
        "scored": sum(c["n"] for cells in models.values() for c in cells.values()),
        "models": models,
        "first_issue": issues[0] if issues else None,
        "logged_issues": len(issues),
        "first_scoreable_month": months[0] if months else None,
        "first_scoreable_month_label": month_label(pd.Period(months[0], freq="M")) if months else None,
    }


def build(actuals, reservoirs, umm_rows, fuel_rows, futures_rows, outlook, now: datetime | None = None) -> dict:
    now = now or now_local()
    result = run(actuals, reservoirs, umm_rows, fuel_rows, now)
    default = result["default_model"]
    band = result["band"].get(default, {})
    monthly = result["context"].monthly
    snapshot = latest_snapshot(futures_rows, to_local(now).date().isoformat())

    zones: dict[str, dict] = {}
    first_any: dict = {}
    for zone, meta in ZONES.items():
        rows = sorted((r for r in result["live"] if r["zone"] == zone), key=lambda r: r["horizon"])
        months = []
        for row in rows:
            period = pd.Period(row["target"], freq="M")
            models: dict[str, dict] = {}
            for model in BACKTESTED:
                value = number(row.get(model))
                if value is not None:
                    models[model] = {"p50": r3(value)}
            spread = band.get(zone, {}).get(str(row["horizon"]))
            if default in models and spread:
                models[default]["p10"] = r3(models[default]["p50"] + spread[0])
                models[default]["p90"] = r3(models[default]["p50"] + spread[1])
            market = implied_month_price(snapshot, zone, period)
            if market:
                models[MARKET] = {"p50": market["price"], **{k: market[k] for k in (
                    "tenor", "delivery", "contracts", "trade_date", "system", "epad")}}
            last_year = None
            if period - 12 in monthly.index and zone in monthly:
                value = monthly.at[period - 12, zone]
                last_year = None if pd.isna(value) else r3(float(value))
            months.append(
                {
                    "month": str(period),
                    "label": month_label(period),
                    "horizon": row["horizon"],
                    "models": models,
                    "last_year": last_year,
                    "nuclear_out_mw": r3(row["nuclear_out_mw"]),
                    "outlook": next((w for w in (outlook or {}).get(zone, []) if w["month"] == str(period)), None),
                }
            )
        first = rows[0] if rows else {}
        first_any = first_any or first
        zones[zone] = {
            "zone": zone,
            "name": meta["name"],
            "recent_30d": r3(number(first.get("recent"))),
            "year_mean": r3(number(first.get("year_mean"))),
            "months": months,
            "drivers_sv": _drivers_sv(zone, first, months, default),
            "fundamental_coefficients": first.get("coefficients"),
        }

    return {
        "generated_at": iso(now),
        "unit": CURRENCY,
        "horizon_months": LONGTERM_HORIZON_MONTHS,
        "default_model": default,
        "reference_model": PERSISTENCE,
        "models": describe_models(default),
        "zones": zones,
        "inputs": {
            "hydro_anomaly": r3(number(first_any.get("hydro_anomaly"))),
            "ttf_eur_mwh": r3(number(first_any.get("ttf"))),
            "eua_eur_t": r3(number(first_any.get("eua"))),
            "gas_plant_cost_eur_mwh": r3(number(first_any.get("gas_cost"))),
            "damping": list(first_any.get("damping") or ()),
            "futures_trade_date": snapshot[0]["trade_date"] if snapshot else None,
        },
        "sources_sv": SOURCES_SV,
        "backtest": result["summary"],
        "accuracy": score(list(read_jsonl(LONGTERM_FORECASTS_PATH)), monthly),
    }


def record(payload: dict, now: datetime) -> int:
    """Append today's forecast once per local day. Earlier rows are never rewritten."""
    today = to_local(now).date().isoformat()
    if any(r.get("issue_date") == today for r in read_jsonl(LONGTERM_FORECASTS_PATH)):
        return 0
    rows = [
        {
            "issued_at": iso(now),
            "issue_date": today,
            "zone": zone,
            "month": month["month"],
            "horizon": month["horizon"],
            "model_id": model,
            "p50": cell.get("p50"),
            "p10": cell.get("p10"),
            "p90": cell.get("p90"),
        }
        for zone, block in payload["zones"].items()
        for month in block["months"]
        for model, cell in month["models"].items()
    ]
    return append_jsonl(LONGTERM_FORECASTS_PATH, rows)


def write(payload: dict) -> None:
    from ..publish.api import write_json

    write_json("longterm.json", payload)
    SITE_DATA_DIR.mkdir(parents=True, exist_ok=True)
    (SITE_DATA_DIR / "longterm.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )

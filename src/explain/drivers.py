"""Swedish driver copy: what is actually moving the price right now, per zone.

No LLM in v1. Every sentence is derived from a number in the feature frame, and the
one optional Svenska kraftnät line is extractive — a clause lifted from their page,
never a claim we invented.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta

import pandas as pd

from ..config import ZONES
from ..store import r3
from ..timeutil import now_local

log = logging.getLogger(__name__)

LOOKAHEAD_HOURS = 48
SVK_KEYWORDS = ("kärnkraft", "ledning", "magasin", "avbrott", "Snitt")
SVK_MAX_CHARS = 220

REGIME_LABELS_SV = {
    "windy_cheap": "Blåsigt och billigt",
    "cold_tight": "Kallt och ansträngt",
    "north_split": "Delat land",
    "south_solar": "Sol i söder",
    "normal": "Normalläge",
}


def _mean(frame: pd.DataFrame, column: str) -> float | None:
    if frame.empty or column not in frame.columns:
        return None
    value = frame[column].mean()
    return None if pd.isna(value) else float(value)


def _snapshot(features: pd.DataFrame, zone: str, now: datetime) -> dict:
    """Next 48 h for this zone, plus the week behind it for comparison."""
    start = pd.Timestamp(now)
    end = pd.Timestamp(now + timedelta(hours=LOOKAHEAD_HOURS))
    week_start = pd.Timestamp(now - timedelta(days=7))

    zone_rows = features[features["zone"] == zone]
    ahead = zone_rows[(zone_rows["ts"] >= start) & (zone_rows["ts"] < end)]
    behind = zone_rows[(zone_rows["ts"] >= week_start) & (zone_rows["ts"] < start)]
    daytime = ahead[(ahead["hour"] >= 9) & (ahead["hour"] <= 16)]

    return {
        "wind_index_local": _mean(ahead, "wind_index_local"),
        "wind_index_north": _mean(ahead, "wind_index_north"),
        "wind_index_south": _mean(ahead, "wind_index_south"),
        "temp_local": _mean(ahead, "temp_local"),
        "temp_anomaly_local": _mean(ahead, "temp_anomaly_local"),
        "solar_index_daytime": _mean(daytime, "solar_index_local"),
        "price_last_week": _mean(behind, "actual_price"),
        "has_holiday": bool(ahead["is_holiday_se"].any()) if not ahead.empty else False,
        "has_weekend": bool(ahead["is_weekend"].any()) if not ahead.empty else False,
    }


def classify_regime(zone: str, snap: dict) -> str:
    wind_local = snap.get("wind_index_local")
    wind_north = snap.get("wind_index_north")
    wind_south = snap.get("wind_index_south")
    anomaly = snap.get("temp_anomaly_local")
    solar = snap.get("solar_index_daytime")

    if wind_local is not None and anomaly is not None and wind_local > 1.25 and anomaly > -3:
        return "windy_cheap"
    if anomaly is not None and anomaly < -5:
        return "cold_tight"
    if (
        zone in {"SE3", "SE4"}
        and wind_north is not None
        and wind_south is not None
        and wind_north > 1.2
        and wind_south < 1.0
    ):
        return "north_split"
    if zone == "SE4" and solar is not None and solar > 0.55:
        return "south_solar"
    return "normal"


HEADLINES_SV = {
    "windy_cheap": "Blåsigt kommande dygn — vinden pressar ner priset i {zone}.",
    "cold_tight": "Kallt väder kommande dygn — högre förbrukning lyfter priset i {zone}.",
    "north_split": "Norr billigare än söder — mycket vind i SE1/SE2 och flaskhals söderut.",
    "south_solar": "Mycket sol mitt på dagen — SE4 får en tydlig dagssvacka.",
    "normal": "Inget som sticker ut — {zone} följer ett normalt veckomönster.",
}


def sv_num(value: float | None, decimals: int = 2, sign: bool = False) -> str:
    """Swedish number formatting: decimal comma, optional explicit sign."""
    if value is None:
        return "–"
    text = f"{value:+.{decimals}f}" if sign else f"{value:.{decimals}f}"
    return text.replace(".", ",")


def _format_index(value: float | None) -> str:
    return sv_num(value, 2)


def _bullets(zone: str, regime: str, snap: dict, spread: float | None, svk_bullet: str | None) -> list[str]:
    bullets: list[str] = []
    wind_local = snap.get("wind_index_local")
    wind_north = snap.get("wind_index_north")
    wind_south = snap.get("wind_index_south")
    anomaly = snap.get("temp_anomaly_local")
    solar = snap.get("solar_index_daytime")

    if wind_local is not None:
        if wind_local > 1.15:
            bullets.append(
                f"Vindprognosen i {zone} ligger {_format_index(wind_local)} gånger det normala "
                "kommande två dygn."
            )
        elif wind_local < 0.85:
            bullets.append(
                f"Det blåser lite i {zone} — vindindex {_format_index(wind_local)} mot normalt 1,00."
            )
        else:
            bullets.append(f"Vinden i {zone} är nära normal, vindindex {_format_index(wind_local)}.")

    if anomaly is not None:
        if anomaly < -3:
            bullets.append(
                f"Temperaturen ligger {sv_num(abs(anomaly), 1)} grader under normalt, "
                "vilket drar upp förbrukningen."
            )
        elif anomaly > 3:
            bullets.append(
                f"Temperaturen ligger {sv_num(anomaly, 1)} grader över normalt, "
                "vilket dämpar uppvärmningsbehovet."
            )
        else:
            bullets.append("Temperaturen är nära det normala för årstiden.")

    if wind_north is not None and wind_south is not None:
        if wind_north - wind_south > 0.25:
            bullets.append(
                f"Vindindex norr {_format_index(wind_north)} mot söder {_format_index(wind_south)} — "
                "när det blåser i Norrland och snittet söderut är trångt sjunker SE1/SE2 medan "
                "SE3/SE4 kan stanna kvar."
            )
        elif wind_south - wind_north > 0.25:
            bullets.append(
                f"Det blåser mer i söder ({_format_index(wind_south)}) än i norr "
                f"({_format_index(wind_north)}), vilket brukar minska skillnaden mellan områdena."
            )

    if zone == "SE4" and solar is not None and solar > 0.4:
        bullets.append(
            f"Solindex mitt på dagen är {_format_index(solar)}. SE4 brukar följa Danmark och "
            "Tyskland mer än SE1."
        )

    if spread is not None:
        bullets.append(
            f"Prognosen ger i snitt {sv_num(spread, 1, sign=True)} EUR/MWh skillnad mellan "
            "SE4 och SE2 kommande två dygn."
        )

    if snap.get("has_holiday"):
        bullets.append("En röd dag ligger inom prognosfönstret — lasten blir lägre den dagen.")
    elif snap.get("has_weekend"):
        bullets.append("Helgen ingår i fönstret, med lägre industriförbrukning än vardagar.")

    if svk_bullet:
        bullets.append(svk_bullet)

    return bullets[:5]


def svk_bullet(text: str | None) -> str | None:
    """One factual clause from Svenska kraftnät's driftinfo, quoted, not paraphrased."""
    if not text:
        return None
    for sentence in re.split(r"(?<=[.!?])\s+|\n", text):
        clean = sentence.strip()
        # Skip fragments and list intros ("... till exempel:") — they read as
        # broken quotes once lifted out of the page.
        if len(clean) < 60 or len(clean.split()) < 9 or clean.rstrip(".").endswith(":"):
            continue
        if any(keyword.lower() in clean.lower() for keyword in SVK_KEYWORDS):
            if len(clean) > SVK_MAX_CHARS:
                clean = clean[:SVK_MAX_CHARS].rsplit(" ", 1)[0] + "…"
            return f"Svenska kraftnät skriver: ”{clean}”"
    return None


def _spread_proxy(series_by_zone: dict[str, list[dict]], now: datetime) -> float | None:
    """Mean forecast SE4 minus SE2 over the next 48 h, from the published ensemble."""
    end = now + timedelta(hours=LOOKAHEAD_HOURS)

    def mean_p50(zone: str) -> float | None:
        points = [
            p["p50"]
            for p in series_by_zone.get(zone, [])
            if p["p50"] is not None and now <= p["ts"] < end
        ]
        return sum(points) / len(points) if points else None

    south, north = mean_p50("SE4"), mean_p50("SE2")
    if south is None or north is None:
        return None
    return south - north


def build_drivers(
    features: pd.DataFrame,
    ensemble_by_zone: dict[str, list[dict]],
    svk_text: str | None = None,
    now: datetime | None = None,
) -> dict[str, dict]:
    """Driver block per zone, matching the `drivers` object in the forecast API."""
    now = now or now_local()
    spread = _spread_proxy(ensemble_by_zone, now)
    extra = svk_bullet(svk_text)

    out: dict[str, dict] = {}
    for zone in ZONES:
        snap = _snapshot(features, zone, now)
        regime = classify_regime(zone, snap)
        out[zone] = {
            "regime": regime,
            "regime_label_sv": REGIME_LABELS_SV[regime],
            "headline_sv": HEADLINES_SV[regime].format(zone=zone),
            "bullets_sv": _bullets(zone, regime, snap, spread, extra),
            "features": {
                "wind_index_local": r3(snap["wind_index_local"]),
                "wind_index_north": r3(snap["wind_index_north"]),
                "wind_index_south": r3(snap["wind_index_south"]),
                f"temp_anomaly_{zone.lower()}_c": r3(snap["temp_anomaly_local"]),
                "temp_local_c": r3(snap["temp_local"]),
                "solar_index_daytime": r3(snap["solar_index_daytime"]),
                "spread_proxy_se4_se2": r3(spread),
            },
        }
    return out


def global_blurb(drivers: dict[str, dict], degraded: bool) -> str:
    """One short sentence for the homepage, above the zone tiles."""
    if degraded:
        return (
            "Körningen är degraderad — en datakälla svarade inte. Siffrorna nedan kan vara "
            "från en tidigare körning."
        )

    regimes = {zone: block["regime"] for zone, block in drivers.items()}
    if regimes.get("SE3") == "north_split" or regimes.get("SE4") == "north_split":
        return (
            "Norr och söder går isär: mycket vind i SE1/SE2 medan överföringen söderut "
            "begränsar hur mycket av den som når SE3 och SE4."
        )
    if any(regime == "cold_tight" for regime in regimes.values()):
        return "Kylan driver förbrukningen uppåt och lyfter prisnivån i hela landet."
    if all(regime == "windy_cheap" for regime in regimes.values()):
        return "Det blåser i hela landet kommande dygn, vilket pressar priserna i alla fyra elområden."
    if any(regime == "windy_cheap" for regime in regimes.values()):
        windy = ", ".join(zone for zone, regime in regimes.items() if regime == "windy_cheap")
        return f"Vinden pressar priset i {windy}. Övriga områden följer ett normalt veckomönster."
    return "Lugnt läge i elsystemet — priserna följer det vanliga dygns- och veckomönstret."

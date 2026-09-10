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
from ..timeutil import now_local, parse_iso

log = logging.getLogger(__name__)

LOOKAHEAD_HOURS = 48
SVK_KEYWORDS = ("kärnkraft", "ledning", "magasin", "avbrott", "Snitt")
SVK_MAX_CHARS = 220

MONTHS_SV = {
    1: "jan", 2: "feb", 3: "mars", 4: "apr", 5: "maj", 6: "juni",
    7: "juli", 8: "aug", 9: "sep", 10: "okt", 11: "nov", 12: "dec",
}

# A reactor block is worth naming ahead of a bigger corridor restriction: it
# removes generation outright, where a corridor only moves it.
NUCLEAR_PRIORITY_MW = 5000

# Foreign bidding zones by name. Swedish zones are known to readers by code.
AREA_NAMES_SV = {
    "FI": "Finland",
    "PL": "Polen",
    "LT": "Litauen",
    "EE": "Estland",
    "DE-LU": "Tyskland",
    "DK1": "Västdanmark",
    "DK2": "Östdanmark",
    "NO1": "Sydöstra Norge",
    "NO2": "Sydvästra Norge",
    "NO3": "Mellersta Norge",
    "NO4": "Norra Norge",
    "NO5": "Västra Norge",
}

COUNT_WORDS_SV = {2: "Två", 3: "Tre", 4: "Fyra", 5: "Fem", 6: "Sex"}

REGIME_LABELS_SV = {
    "outage_tight": "Bortfall i systemet",
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
    "outage_tight": "{outage_headline}",
    "windy_cheap": "Blåsigt kommande dygn — vinden pressar ner priset i {zone}.",
    "cold_tight": "Kallt väder kommande dygn — högre förbrukning lyfter priset i {zone}.",
    "north_split": "Norr billigare än söder — mycket vind i SE1/SE2 och flaskhals söderut.",
    "south_solar": "Mycket sol mitt på dagen — SE4 får en tydlig dagssvacka.",
    "normal": "Inget som sticker ut — {zone} följer ett normalt veckomönster.",
}


def sv_num(value: float | None, decimals: int = 2, sign: bool = False) -> str:
    """Swedish number formatting: decimal comma, non-breaking thousands space."""
    if value is None:
        return "–"
    text = f"{value:+,.{decimals}f}" if sign else f"{value:,.{decimals}f}"
    return text.replace(",", "\u00a0").replace(".", ",")


def sv_date(when: datetime, with_time: bool = False) -> str:
    """Swedish short date; strftime would render English month names here."""
    base = f"{when.day} {MONTHS_SV[when.month]}"
    return f"{base} {when:%H:%M}" if with_time else base


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
        # A price difference between two zones exists only while the corridor
        # between them is full, which is the one thing this number says.
        if abs(spread) < 3:
            bullets.append(
                "SE4 och SE2 väntas få nästan samma pris kommande två dygn — "
                "överföringen söderut räcker till."
            )
        elif spread > 0:
            bullets.append(
                f"SE4 väntas i snitt bli {sv_num(spread, 1)} EUR/MWh dyrare än SE2 kommande "
                "två dygn. En sådan skillnad uppstår när överföringen söderut är fullt utnyttjad."
            )
        else:
            bullets.append(
                f"SE4 väntas i snitt bli {sv_num(abs(spread), 1)} EUR/MWh billigare än SE2 "
                "kommande två dygn."
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


def _area(code: str | None) -> str:
    return AREA_NAMES_SV.get(code or "", code or "?")


def _unit_name(name: str) -> str:
    """'Ringhals Block4' and 'Forsmark block 1' read as 'Ringhals 4' and 'Forsmark 1'."""
    return re.sub(r"\s*[Bb]lock\s*(\d+)", r" \1", name or "").strip()


def _period(item: dict, now: datetime) -> str:
    start, stop = parse_iso(item["from"]), parse_iso(item["to"])
    if start <= now:
        return f"fram till {sv_date(stop)}"
    return f"från {sv_date(start, with_time=True)} till {sv_date(stop)}"


def _join_sv(parts: list[str]) -> str:
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " och " + parts[-1]


def _home(items: list[dict], zone: str) -> str:
    """' i SE3' when every item sits in one other zone, else ''."""
    homes = {i.get("zone") for i in items}
    if len(homes) == 1 and zone not in homes and None not in homes:
        return f" i {next(iter(homes))}"
    return ""


def outage_bullets(block: dict | None, zone: str = "", now: datetime | None = None) -> list[str]:
    """Name what is actually out. These are published facts, not model output.

    Deliberately placed first among the bullets: a gigawatt of nuclear leaving
    the system matters more to next week's price than any wind index, and the
    forecast underneath does not yet know about it. Reactors share one bullet,
    so a corridor restriction still gets a line of its own.
    """
    if not block or not block.get("items"):
        return []
    now = now or now_local()

    items = rank_outages(block["items"])
    nuclear = [i for i in items if i.get("nuclear")]
    others = [i for i in items if not i.get("nuclear")]

    lines: list[str] = []
    if nuclear:
        home = _home(nuclear, zone)
        parts = []
        for item in nuclear:
            tag = f" ({item['zone']})" if not home and item.get("zone") not in (zone, None) else ""
            parts.append(
                f"{_unit_name(item['unit'])}{tag} ({sv_num(item['unavailable_mw'], 0)} MW, "
                f"{_period(item, now)})"
            )
        lines.append(f"Kärnkraft ur drift{home}: {_join_sv(parts)}.")

    for item in others[: 3 - len(lines)]:
        mw = item["unavailable_mw"]
        if item["kind"] == "production":
            lines.append(
                f"Anläggningen {item['unit']} ({item['fuel']}) är ur drift med "
                f"{sv_num(mw, 0)} MW {_period(item, now)}."
            )
        else:
            share = ""
            if item.get("installed_mw"):
                share = f", {sv_num(100 * mw / item['installed_mw'], 0)} % av kapaciteten"
            lines.append(
                f"Överföringen från {_area(item.get('from_area'))} till "
                f"{_area(item.get('to_area'))} är begränsad med {sv_num(mw, 0)} MW{share}, "
                f"{_period(item, now)}."
            )
    return lines


def reservoir_bullet(state: dict | None) -> str | None:
    """Reservoir fill as a fact for the reader, not as model input.

    It was tested both as a level adjustment and as an amplifier of the weather
    effect and improved neither, so no model consumes it. It still tells a
    reader something true about how much slack the system has.
    """
    if not state:
        return None
    fill = state.get("fill_ratio")
    anomaly = state.get("week_anomaly")
    if fill is None:
        return None
    if anomaly is None:
        return f"Vattenmagasinen är fyllda till {sv_num(100 * fill, 0)} %."
    direction = "under" if anomaly < 0 else "över"
    return (
        f"Vattenmagasinen är fyllda till {sv_num(100 * fill, 0)} %, "
        f"{sv_num(abs(100 * anomaly), 0)} procentenheter {direction} det normala för "
        "årstiden."
    )


def rank_outages(items: list[dict]) -> list[dict]:
    """Order by what a reader needs told first, not by raw megawatts."""
    return sorted(
        items,
        key=lambda i: -((i.get("unavailable_mw") or 0) + (NUCLEAR_PRIORITY_MW if i.get("nuclear") else 0)),
    )


def outage_headline(zone: str, block: dict | None) -> str | None:
    """A summary above the bullets: it names the situation, they give the figures.

    It used to restate the first bullet almost word for word, which read as the
    same event listed twice.
    """
    if not block or not block.get("items"):
        return None
    items = rank_outages(block["items"])
    nuclear = [i for i in items if i.get("nuclear")]
    if nuclear:
        home = _home(nuclear, zone)
        partly = any(
            i.get("installed_mw") and i["unavailable_mw"] < i["installed_mw"] for i in nuclear
        )
        state = "helt eller delvis ur drift" if partly else "ur drift"
        if len(nuclear) == 1:
            subject = f"Kärnkraftsblocket {_unit_name(nuclear[0]['unit'])}{home} är {state}"
        else:
            count = COUNT_WORDS_SV.get(len(nuclear), str(len(nuclear)))
            subject = f"{count} kärnkraftsblock{home} är {state}"
        return f"{subject} — det stramar åt {zone}."
    transmission = [i for i in items if i["kind"] == "transmission"]
    if transmission and (transmission[0].get("unavailable_mw") or 0) >= 1000:
        top = transmission[0]
        return (
            f"Begränsad överföring från {_area(top.get('from_area'))} till "
            f"{_area(top.get('to_area'))} påverkar priset i {zone}."
        )
    return None


def build_drivers(
    features: pd.DataFrame,
    ensemble_by_zone: dict[str, list[dict]],
    svk_text: str | None = None,
    now: datetime | None = None,
    outages: dict[str, dict] | None = None,
    reservoirs: dict[str, dict] | None = None,
) -> dict[str, dict]:
    """Driver block per zone, matching the `drivers` object in the forecast API."""
    now = now or now_local()
    spread = _spread_proxy(ensemble_by_zone, now)
    extra = svk_bullet(svk_text)

    out: dict[str, dict] = {}
    for zone in ZONES:
        snap = _snapshot(features, zone, now)
        regime = classify_regime(zone, snap)
        outage_block = (outages or {}).get(zone)
        outage_lines = outage_bullets(outage_block, zone, now)
        water = reservoir_bullet((reservoirs or {}).get(zone))
        if water:
            outage_lines = outage_lines + [water]
        headline = outage_headline(zone, outage_block)
        if headline:
            regime = "outage_tight"
        out[zone] = {
            "regime": regime,
            "regime_label_sv": REGIME_LABELS_SV[regime],
            "headline_sv": headline or HEADLINES_SV[regime].format(zone=zone),
            "bullets_sv": outage_lines + _bullets(zone, regime, snap, spread, extra)[: 5 - len(outage_lines)],
            "outages": outage_block or {"items": []},
            "reservoir": (reservoirs or {}).get(zone),
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

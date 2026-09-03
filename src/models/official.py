"""The published day-ahead outcome. Not a model and never scored as one.

Kept beside the models so the publishing layer has one obvious place to ask
"is this hour fact or forecast?".
"""

from __future__ import annotations

from datetime import datetime

from ..timeutil import parse_iso

SOURCE_OFFICIAL = "official"
SOURCE_FORECAST = "forecast"
SOURCE_DEMO = "demo"


class Official:
    id = "official"
    name_sv = "Officiellt utfall"
    description_sv = (
        "Nord Pools publicerade day-ahead-pris. Det är inte en prognos utan facit, "
        "och räknas aldrig som någon modells träffsäkerhet."
    )
    quantiles = False
    derived = True


def actuals_index(actuals: list[dict]) -> dict[tuple[str, datetime], float]:
    """(zone, ts) -> price, for merging fact into the published series."""
    index: dict[tuple[str, datetime], float] = {}
    for row in actuals:
        index[(row["zone"], parse_iso(row["ts"]))] = float(row["price_eur_mwh"])
    return index

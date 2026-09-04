"""Report what each upstream source actually offers.

    python -m src.diagnose            # every source
    python -m src.diagnose --supply   # only the ENTSO-E supply-side series

Written to answer questions that cannot be answered from documentation: how far
back day-ahead prices really go for a given token, whether hydro reservoir levels
exist per Swedish bidding zone or only per country, and what shape outage records
come back in. Runs in CI where the token lives, so its output is a workflow log.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import warnings
from datetime import timedelta

from .config import ZONES
from .timeutil import now_local

log = logging.getLogger("diagnose")


def rule(title: str) -> None:
    print(f"\n{'=' * 72}\n{title}\n{'=' * 72}")


def _client():
    from entsoe import EntsoePandasClient

    return EntsoePandasClient(api_key=os.environ["ENTSOE_TOKEN"])


def _areas() -> dict[str, str | None]:
    from .fetch.entsoe_fundamentals import _resolve_area

    return {zone: _resolve_area(zone) for zone in ZONES}


def probe_prices() -> None:
    from .fetch.entsoe_prices import fetch_zone

    rule("Day-ahead prices — how far back does this token reach?")
    now = now_local()
    for years in (1, 2, 3, 5, 8):
        start = now - timedelta(days=365 * years)
        try:
            rows = fetch_zone("SE3", start, start + timedelta(days=2))
            state = f"{len(rows)} hours" if rows else "empty"
        except Exception as exc:  # noqa: BLE001
            state = f"FAIL {type(exc).__name__}: {str(exc)[:70]}"
        print(f"  {years} year(s) back ({start:%Y-%m-%d}): {state}")


def probe_reservoirs() -> None:
    import pandas as pd

    rule("Hydro reservoirs — per bidding zone, and how deep?")
    client, now = _client(), now_local()
    for zone, area in _areas().items():
        if area is None:
            print(f"  {zone}: no area alias")
            continue
        try:
            series = client.query_aggregate_water_reservoirs_and_hydro_storage(
                area, start=pd.Timestamp(now - timedelta(days=365 * 4)), end=pd.Timestamp(now)
            )
        except Exception as exc:  # noqa: BLE001
            print(f"  {zone}: FAIL {type(exc).__name__}: {str(exc)[:70]}")
            continue
        if series is None or len(series) == 0:
            print(f"  {zone}: empty")
            continue
        steps = pd.Series(series.index).diff().dropna().mode()
        print(
            f"  {zone}: {len(series)} points, {series.index.min():%Y-%m-%d} -> "
            f"{series.index.max():%Y-%m-%d}, step {steps.iloc[0] if len(steps) else '?'}"
        )
        print(f"      min {series.min():,.0f}  max {series.max():,.0f}  latest {series.iloc[-1]:,.0f}")


def probe_outages() -> None:
    import pandas as pd

    rule("Generation and production outages — shape and volume")
    client, now = _client(), now_local()
    start = pd.Timestamp(now - timedelta(days=60))
    end = pd.Timestamp(now + timedelta(days=60))

    for label, method in (
        ("production units", "query_unavailability_of_production_units"),
        ("generation units", "query_unavailability_of_generation_units"),
    ):
        print(f"\n  -- {label} --")
        for zone, area in _areas().items():
            if area is None:
                continue
            try:
                frame = getattr(client, method)(area, start=start, end=end)
            except Exception as exc:  # noqa: BLE001
                print(f"  {zone}: FAIL {type(exc).__name__}: {str(exc)[:70]}")
                continue
            if frame is None or len(frame) == 0:
                print(f"  {zone}: empty")
                continue
            print(f"  {zone}: {len(frame)} records")
            print(f"      columns: {list(frame.columns)}")
            print(frame.head(2).to_string(max_colwidth=20))
            break  # one zone is enough to learn the shape


def probe_outages_raw() -> None:
    """Go under entsoe-py to see what the unavailability endpoint really returns.

    The library unzips the response; a BadZipFile tells us nothing about whether
    the cause is "no data", "not entitled" or a changed payload.
    """
    from .config import ENTSOE_API_URL, ZONES
    from .fetch.http import get

    rule("Unavailability endpoint — raw response")
    now = now_local()
    window = (now - timedelta(days=30), now + timedelta(days=30))

    for doc_type, label in (("A80", "generation units"), ("A77", "production units")):
        for zone in ("SE3",):
            params = {
                "documentType": doc_type,
                "biddingZone_Domain": ZONES[zone]["eic"],
                "periodStart": window[0].strftime("%Y%m%d%H%M"),
                "periodEnd": window[1].strftime("%Y%m%d%H%M"),
                "securityToken": os.environ["ENTSOE_TOKEN"],
            }
            try:
                response = get(ENTSOE_API_URL, params=params, retries=1)
            except Exception as exc:  # noqa: BLE001
                print(f"  {doc_type} {label} {zone}: HTTP FAIL {str(exc)[:100]}")
                continue
            body = response.content
            kind = "ZIP" if body[:2] == b"PK" else "not a zip"
            print(
                f"  {doc_type} {label} {zone}: HTTP {response.status_code}, "
                f"{len(body)} bytes, {kind}, content-type={response.headers.get('content-type')}"
            )
            if kind != "ZIP":
                print("      " + response.text[:400].replace("\n", " "))


def probe_transmission() -> None:
    """Interconnector state: capacity offered, and cables reported out.

    A cable out of service moves a zone within the hour, the way a reactor trip
    does, so this is the half of the supply side that fits inside a seven-day
    forecast window.
    """
    import pandas as pd

    rule("Transmission — day-ahead capacity and cable outages")
    client, now = _client(), now_local()
    start = pd.Timestamp(now - timedelta(days=14))
    end = pd.Timestamp(now + timedelta(days=2))

    # The borders that actually price SE3 and SE4.
    borders = [("SE_3", "SE_4"), ("SE_2", "SE_3"), ("SE_4", "DE_LU"), ("SE_3", "FI"), ("SE_4", "DK_2")]

    print("\n  -- net transfer capacity, day-ahead --")
    for a, b in borders:
        try:
            series = client.query_net_transfer_capacity_dayahead(a, b, start=start, end=end)
            if series is None or len(series) == 0:
                print(f"  {a}->{b}: empty")
                continue
            print(
                f"  {a}->{b}: {len(series)} points, min {series.min():,.0f} "
                f"max {series.max():,.0f} MW, latest {series.iloc[-1]:,.0f}"
            )
        except Exception as exc:  # noqa: BLE001
            print(f"  {a}->{b}: FAIL {type(exc).__name__}: {str(exc)[:70]}")

    print("\n  -- transmission unavailability --")
    for a, b in borders[:3]:
        try:
            frame = client.query_unavailability_transmission(a, b, start=start, end=end)
            if frame is None or len(frame) == 0:
                print(f"  {a}->{b}: empty")
                continue
            print(f"  {a}->{b}: {len(frame)} records, columns {list(frame.columns)}")
        except Exception as exc:  # noqa: BLE001
            print(f"  {a}->{b}: FAIL {type(exc).__name__}: {str(exc)[:70]}")


def probe_other() -> None:
    rule("Other sources")
    from .fetch import ecb_fx, open_meteo, svk_text

    fx, status = ecb_fx.fetch_eur_sek()
    print(f"  ECB FX          : {status}")
    weather, status = open_meteo.fetch_weather()
    print(f"  Open-Meteo      : {status}")
    _, status = svk_text.fetch_svk_text()
    print(f"  SVK driftinfo   : {status}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Report upstream data availability")
    parser.add_argument("--supply", action="store_true", help="only the supply-side probes")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    warnings.filterwarnings("ignore")

    if not os.environ.get("ENTSOE_TOKEN", "").strip():
        print("ENTSOE_TOKEN is not set — nothing to probe.")
        return 1

    print(f"Areas resolved against the installed entsoe-py: {_areas()}")
    if not args.supply:
        probe_prices()
    probe_reservoirs()
    probe_outages()
    probe_outages_raw()
    probe_transmission()
    if not args.supply:
        probe_other()
    return 0


if __name__ == "__main__":
    sys.exit(main())

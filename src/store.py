"""Append-only JSONL state: official actuals and issued forecasts."""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Iterator

from .config import (
    ACTUALS_DIR,
    FORECASTS_DIR,
    HORIZON_HOURS,
    LEGACY_ACTUALS_PATH,
    LEGACY_FORECASTS_PATH,
    QUARTERS_DIR,
    QUARTER_RETAIN_DAYS,
    RESERVOIRS_PATH,
    UMM_DIR,
    ROUND_DECIMALS,
)
from .timeutil import TZ, now_local, parse_iso, to_local

log = logging.getLogger(__name__)


def read_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    if not path.exists():
        return iter(())

    def _gen() -> Iterator[dict[str, Any]]:
        with path.open("r", encoding="utf-8") as fh:
            for line_no, line in enumerate(fh, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    log.warning("Skipping malformed line %s in %s", line_no, path.name)

    return _gen()


def append_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("a", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
            count += 1
    return count


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    count = 0
    with tmp.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
            count += 1
    tmp.replace(path)
    return count


def r3(value: float | None) -> float | None:
    if value is None:
        return None
    return round(float(value), ROUND_DECIMALS)


# ---------------------------------------------------------------- actuals
#
# Partitioned one file per delivery year. Compacting the whole history on every
# run would rewrite a multi-megabyte blob three times a day; only the years a
# run actually touches are rewritten.


def _year_path(year: int) -> Path:
    return ACTUALS_DIR / f"{year}.jsonl"


def _migrate_legacy_actuals() -> None:
    """Split a pre-partition data/actuals.jsonl into per-year files, once."""
    if not LEGACY_ACTUALS_PATH.exists():
        return
    rows = list(read_jsonl(LEGACY_ACTUALS_PATH))
    if rows:
        _write_years(_group_by_year(rows))
        log.info("Migrated %s rows from actuals.jsonl into %s", len(rows), ACTUALS_DIR)
    LEGACY_ACTUALS_PATH.unlink()


def _group_by_year(rows: Iterable[dict[str, Any]]) -> dict[int, list[dict[str, Any]]]:
    grouped: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(parse_iso(row["ts"]).year, []).append(row)
    return grouped


def _write_years(grouped: dict[int, list[dict[str, Any]]]) -> None:
    for year, rows in grouped.items():
        ordered = sorted(rows, key=lambda r: (r["zone"], parse_iso(r["ts"])))
        write_jsonl(_year_path(year), ordered)


def actual_years() -> list[int]:
    if not ACTUALS_DIR.exists():
        return []
    years = []
    for path in ACTUALS_DIR.glob("*.jsonl"):
        try:
            years.append(int(path.stem))
        except ValueError:
            continue
    return sorted(years)


def load_actuals(since=None) -> list[dict[str, Any]]:
    """All stored official prices, or only those from `since` onward."""
    _migrate_legacy_actuals()
    rows: list[dict[str, Any]] = []
    for year in actual_years():
        if since is not None and year < since.year:
            continue
        for row in read_jsonl(_year_path(year)):
            if since is not None and parse_iso(row["ts"]) < since:
                continue
            rows.append(row)
    return rows


def upsert_actuals(rows: Iterable[dict[str, Any]]) -> int:
    """Merge new official prices in, keeping one row per (zone, ts).

    Actuals are facts, so a later publication of the same hour simply wins.
    Only the years present in `rows` are read back and rewritten.
    """
    _migrate_legacy_actuals()
    incoming = _group_by_year(rows)
    added = 0

    for year, year_rows in incoming.items():
        merged: dict[tuple[str, str], dict[str, Any]] = {
            (r["zone"], r["ts"]): r for r in read_jsonl(_year_path(year))
        }
        for row in year_rows:
            key = (row["zone"], row["ts"])
            if key not in merged:
                added += 1
            merged[key] = row
        _write_years({year: list(merged.values())})

    return added


# ---------------------------------------------------------------- quarters


def load_quarters() -> list[dict[str, Any]]:
    if not QUARTERS_DIR.exists():
        return []
    rows: list[dict[str, Any]] = []
    for path in sorted(QUARTERS_DIR.glob("*.jsonl")):
        rows.extend(read_jsonl(path))
    return rows


def upsert_quarters(rows: Iterable[dict[str, Any]]) -> int:
    """Native-resolution prices, kept only for a short rolling window.

    Four years of quarters would be 560 000 rows rewritten on every run; the
    published auction window is what anyone can act on, so that is what is kept.
    """
    cutoff = now_local() - timedelta(days=QUARTER_RETAIN_DAYS)
    merged: dict[tuple[str, str], dict[str, Any]] = {
        (r["zone"], r["ts"]): r for r in load_quarters() if parse_iso(r["ts"]) >= cutoff
    }
    added = 0
    for row in rows:
        if parse_iso(row["ts"]) < cutoff:
            continue
        key = (row["zone"], row["ts"])
        if key not in merged:
            added += 1
        merged[key] = row

    QUARTERS_DIR.mkdir(parents=True, exist_ok=True)
    for path in QUARTERS_DIR.glob("*.jsonl"):
        path.unlink()
    ordered = sorted(merged.values(), key=lambda r: (r["zone"], parse_iso(r["ts"])))
    if ordered:
        write_jsonl(QUARTERS_DIR / "recent.jsonl", ordered)
    return added


# ---------------------------------------------------------------- reservoirs


def load_reservoirs() -> list[dict[str, Any]]:
    return list(read_jsonl(RESERVOIRS_PATH))


def upsert_reservoirs(rows: Iterable[dict[str, Any]]) -> int:
    """Weekly readings, unique on (zone, ts). Small enough to rewrite whole."""
    merged: dict[tuple[str, str], dict[str, Any]] = {
        (r["zone"], r["ts"]): r for r in read_jsonl(RESERVOIRS_PATH)
    }
    added = 0
    for row in rows:
        key = (row["zone"], row["ts"])
        if key not in merged:
            added += 1
        merged[key] = row
    ordered = sorted(merged.values(), key=lambda r: (r["zone"], parse_iso(r["ts"])))
    write_jsonl(RESERVOIRS_PATH, ordered)
    return added


# ---------------------------------------------------------------- forecasts
#
# Partitioned one file per ISO week of issue, in Stockholm time. A single log
# grew 0.8 MiB a run and would have passed GitHub's 100 MiB push limit about a
# month after launch, and the old size-triggered rotation could not help: it
# only moved rows older than 180 days. A week stays near 20 MiB however long the
# site runs, and a run only ever appends to the current one.


def _forecast_week_path(issued: datetime) -> Path:
    year, week, _ = to_local(issued).isocalendar()
    return FORECASTS_DIR / f"{year}-W{week:02d}.jsonl"


def _forecast_week_start(path: Path) -> datetime | None:
    year, _, week = path.stem.partition("-W")
    try:
        day = date.fromisocalendar(int(year), int(week), 1)
    except ValueError:
        return None
    return datetime(day.year, day.month, day.day, tzinfo=TZ)


def _forecast_key(row: dict[str, Any]) -> tuple:
    return (row["issued_at"], row["model_id"], row["zone"], row["ts"])


def _migrate_legacy_forecasts() -> None:
    """Split a pre-partition data/forecasts.jsonl into weekly files, once.

    Keyed on (issued_at, model_id, zone, ts), so a migration interrupted
    halfway can run again without duplicating a row.
    """
    if not LEGACY_FORECASTS_PATH.exists():
        return
    grouped: dict[Path, list[dict[str, Any]]] = {}
    for row in read_jsonl(LEGACY_FORECASTS_PATH):
        grouped.setdefault(_forecast_week_path(parse_iso(row["issued_at"])), []).append(row)
    moved = 0
    for path, rows in grouped.items():
        present = {_forecast_key(r) for r in read_jsonl(path)}
        moved += append_jsonl(path, (r for r in rows if _forecast_key(r) not in present))
    LEGACY_FORECASTS_PATH.unlink()
    log.info("Migrated %s rows from forecasts.jsonl into %s", moved, FORECASTS_DIR)


def load_forecasts(since=None) -> list[dict[str, Any]]:
    """Issued forecasts, or only those for delivery hours from `since` onward."""
    _migrate_legacy_forecasts()
    if not FORECASTS_DIR.exists():
        return []
    rows = []
    for path in sorted(FORECASTS_DIR.glob("*.jsonl")):
        week_start = _forecast_week_start(path)
        # Nothing issued in a week targets an hour beyond its end plus the horizon.
        if (
            since is not None
            and week_start is not None
            and week_start + timedelta(days=7, hours=HORIZON_HOURS) < since
        ):
            continue
        for row in read_jsonl(path):
            if since is not None and parse_iso(row["ts"]) < since:
                continue
            rows.append(row)
    return rows


def append_forecasts(rows: Iterable[dict[str, Any]]) -> int:
    """Append issued forecasts to their week's file. Existing rows are never rewritten."""
    _migrate_legacy_forecasts()
    grouped: dict[Path, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(_forecast_week_path(parse_iso(row["issued_at"])), []).append(row)
    return sum(append_jsonl(path, part) for path, part in grouped.items())


# ---------------------------------------------------------------- outages


def _umm_year_path(year: int) -> Path:
    return UMM_DIR / f"{year}.jsonl"


def load_umm(since=None) -> list[dict[str, Any]]:
    """Flattened outage rows, optionally only those published from `since`."""
    if not UMM_DIR.exists():
        return []
    rows: list[dict[str, Any]] = []
    for path in sorted(UMM_DIR.glob("*.jsonl")):
        for row in read_jsonl(path):
            if since is not None and parse_iso(row["published_at"]) < since:
                continue
            rows.append(row)
    return rows


def upsert_umm(rows: Iterable[dict[str, Any]]) -> int:
    """Store outage rows, partitioned by publication year.

    Keyed on the message, its version and the exact event period, so a
    republished message replaces its own row and never duplicates an outage.
    """
    incoming: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        incoming.setdefault(parse_iso(row["published_at"]).year, []).append(row)

    added = 0
    for year, year_rows in incoming.items():
        def key(r: dict[str, Any]) -> tuple:
            return (r["message_id"], r["version"], r.get("unit"), r["event_start"], r["event_stop"])

        merged = {key(r): r for r in read_jsonl(_umm_year_path(year))}
        for row in year_rows:
            if key(row) not in merged:
                added += 1
            merged[key(row)] = row
        ordered = sorted(merged.values(), key=lambda r: (r["published_at"], r.get("unit") or ""))
        write_jsonl(_umm_year_path(year), ordered)
    return added

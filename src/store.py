"""Append-only JSONL state: official actuals and issued forecasts."""

from __future__ import annotations

import json
import logging
from datetime import timedelta
from pathlib import Path
from typing import Any, Iterable, Iterator

from .config import (
    ACTUALS_DIR,
    ARCHIVE_DIR,
    LEGACY_ACTUALS_PATH,
    FORECASTS_PATH,
    FORECAST_RETAIN_DAYS,
    FORECAST_ROTATE_MB,
    ROUND_DECIMALS,
)
from .timeutil import now_local, parse_iso

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


# ---------------------------------------------------------------- forecasts


def load_forecasts(since=None) -> list[dict[str, Any]]:
    rows = []
    for row in read_jsonl(FORECASTS_PATH):
        if since is not None and parse_iso(row["ts"]) < since:
            continue
        rows.append(row)
    return rows


def append_forecasts(rows: Iterable[dict[str, Any]]) -> int:
    """Append issued forecasts. Existing rows are never rewritten."""
    return append_jsonl(FORECASTS_PATH, rows)


def rotate_forecasts() -> str | None:
    """Move forecasts older than the retention window into a yearly archive."""
    if not FORECASTS_PATH.exists():
        return None
    size_mb = FORECASTS_PATH.stat().st_size / (1024 * 1024)
    if size_mb < FORECAST_ROTATE_MB:
        return None

    cutoff = now_local() - timedelta(days=FORECAST_RETAIN_DAYS)
    keep: list[dict[str, Any]] = []
    archived: dict[int, list[dict[str, Any]]] = {}
    for row in read_jsonl(FORECASTS_PATH):
        issued = parse_iso(row["issued_at"])
        if issued < cutoff:
            archived.setdefault(issued.year, []).append(row)
        else:
            keep.append(row)

    if not archived:
        return None

    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    for year, rows in archived.items():
        target = ARCHIVE_DIR / f"forecasts-{year}.jsonl"
        append_jsonl(target, rows)
    write_jsonl(FORECASTS_PATH, keep)
    moved = sum(len(v) for v in archived.values())
    log.info("Rotated %s forecast rows into %s", moved, ARCHIVE_DIR)
    return f"rotated {moved} rows"

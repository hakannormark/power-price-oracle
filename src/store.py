"""Append-only JSONL state: official actuals and issued forecasts."""

from __future__ import annotations

import json
import logging
from datetime import timedelta
from pathlib import Path
from typing import Any, Iterable, Iterator

from .config import (
    ACTUALS_PATH,
    ARCHIVE_DIR,
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


def load_actuals() -> list[dict[str, Any]]:
    return list(read_jsonl(ACTUALS_PATH))


def upsert_actuals(rows: Iterable[dict[str, Any]]) -> int:
    """Merge new official prices in, keeping one row per (zone, ts).

    The file is rewritten sorted and compacted every run; actuals are facts, so
    a later publication of the same hour simply wins.
    """
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for row in read_jsonl(ACTUALS_PATH):
        merged[(row["zone"], row["ts"])] = row

    added = 0
    for row in rows:
        key = (row["zone"], row["ts"])
        if key not in merged:
            added += 1
        merged[key] = row

    ordered = sorted(merged.values(), key=lambda r: (r["zone"], parse_iso(r["ts"])))
    write_jsonl(ACTUALS_PATH, ordered)
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

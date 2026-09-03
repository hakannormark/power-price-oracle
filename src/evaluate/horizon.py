"""Horizon bucketing — the axis the whole accuracy product is organised around."""

from __future__ import annotations

from ..config import HORIZON_BUCKETS, HORIZON_HOURS
from ..timeutil import bucket_for_horizon  # re-exported for convenience

BUCKET_LABELS = [label for label, _, _ in HORIZON_BUCKETS]

BUCKET_LABELS_SV = {
    "0-24h": "0–24 h",
    "24-48h": "24–48 h",
    "48-72h": "48–72 h",
    "72-96h": "72–96 h",
    "96-120h": "96–120 h",
    "120-144h": "120–144 h",
    "144-168h": "144–168 h",
}


def in_scoring_range(horizon_h: int) -> bool:
    return 0 <= horizon_h < HORIZON_HOURS


def bucket_label_sv(label: str) -> str:
    return BUCKET_LABELS_SV.get(label, label)


__all__ = [
    "BUCKET_LABELS",
    "BUCKET_LABELS_SV",
    "bucket_for_horizon",
    "bucket_label_sv",
    "in_scoring_range",
]

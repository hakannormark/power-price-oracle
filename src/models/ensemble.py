"""ensemble: the published default. Derived from base-model output, never re-run."""

from __future__ import annotations

from datetime import datetime

from .base import ForecastPoint, order_quantiles

WEIGHTS = {"seasonal_naive": 0.35, "weather_scaled": 0.65}
P10_MARGIN = 0.98
P90_MARGIN = 1.02


class Ensemble:
    id = "ensemble"
    name_sv = "Ensemble"
    description_sv = (
        "Viktat snitt av säsongsnaiv och väderskalad, 35/65. Var sajtens standardmodell "
        "fram till att den mättes: på 82 576 timmar ut ur urvalet hamnade den på 28,40 "
        "i medelfel mot 25,63 för den dämpade väderskalade modellen. Varje blandning "
        "gjorde resultatet sämre än den bästa modellen ensam, så ensemblen finns kvar "
        "som jämförelse i stället för som standard."
    )
    quantiles = True
    derived = True

    def combine(
        self, predictions: dict[str, list[ForecastPoint]], issued_at: datetime
    ) -> list[ForecastPoint]:
        """Blend base-model points on their shared (zone, ts) keys."""
        by_key: dict[tuple[str, datetime], dict[str, ForecastPoint]] = {}
        for model_id, points in predictions.items():
            if model_id not in WEIGHTS:
                continue
            for point in points:
                by_key.setdefault((point.zone, point.ts), {})[model_id] = point

        combined: list[ForecastPoint] = []
        for (zone, ts), members in sorted(by_key.items(), key=lambda kv: (kv[0][0], kv[0][1])):
            available = {m: p for m, p in members.items() if m in WEIGHTS}
            if not available:
                continue
            total = sum(WEIGHTS[m] for m in available)
            p50 = sum(WEIGHTS[m] * p.p50 for m, p in available.items()) / total
            p10 = min(p.p10 for p in available.values()) * P10_MARGIN
            p90 = max(p.p90 for p in available.values()) * P90_MARGIN
            p10, p50, p90 = order_quantiles(p10, p50, p90)
            combined.append(ForecastPoint(ts=ts, zone=zone, p10=p10, p50=p50, p90=p90))
        return combined

    def predict(self, features, issued_at: datetime) -> list[ForecastPoint]:  # pragma: no cover
        raise NotImplementedError(
            "Ensemble is derived: the pipeline calls combine() with base-model output."
        )

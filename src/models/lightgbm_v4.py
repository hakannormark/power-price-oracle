"""lightgbm_v4: level-blind quantile residuals, one model per horizon regime.

See train_lightgbm_v4.py for what it is and how it was tested, and
lgbm4_features.py for why no raw price level is a feature. A candidate: it is
logged and scored, and becomes the default only by the rule in registry.py.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from ..timeutil import TZ
from . import lgbm3_features as F3
from . import lgbm4_features as F
from .base import ForecastPoint, order_quantiles
from .lightgbm_v3 import serving_inputs, zone_targets

log = logging.getLogger(__name__)

ARTIFACTS_DIR = Path(__file__).resolve().parent / "artifacts"
META = ARTIFACTS_DIR / "meta_v4.json"
REGIMES = ("short", "long")
QUANTILES = ("q10", "q50", "q90")


def _meta() -> dict:
    try:
        return json.loads(META.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _description() -> str:
    text = (
        "Kandidat. Bygger på LightGBM v3 men ser ingen prisnivå: målet och alla "
        "prissignaler räknas i förhållande till senaste veckans pris. v2 och v3 hade "
        "lärt sig att höga priser brukar falla tillbaka och underskattade därför "
        "SE3 och SE4 under hösten 2026. Dygnet närmast efter senast kända pris har "
        "en egen modell, och osäkerhetsbandet kalibreras per elområde."
    )
    meta = _meta()
    try:
        wf = meta["walk_forward"]["overall"]
        text += (
            f" Senast tränad {meta['trained_at'][:10]}. I test på de sex senaste månaderna, "
            f"med omträning före varje månad: medelfel {str(wf['mae_v4']).replace('.', ',')} EUR/MWh mot "
            f"{str(wf['mae_seasonal_naive']).replace('.', ',')} för den säsongsnaiva referensen. "
            "Testet är gjort på arkiverade prognoser, inte på skarpa – det avgörs på träffsäkerhetssidan."
        )
    except (KeyError, TypeError):
        pass
    return text


class LightGbmV4:
    id = "lightgbm_v4"
    name_sv = "LightGBM v4 (kandidat)"
    quantiles = True
    derived = False

    def __init__(self) -> None:
        self._boosters: dict[tuple[str, str], Any] = {}
        self.description_sv = _description()

    def _offsets(self) -> dict[str, dict[str, list[float]]]:
        """Per zone and lead, how far to widen each bound, in units of the scale."""
        return _meta().get("walk_forward", {}).get("conformal_offsets_scaled", {})

    def _load(self) -> bool:
        if self._boosters:
            return True
        try:
            import lightgbm as lgb

            for regime in REGIMES:
                for name in QUANTILES:
                    path = ARTIFACTS_DIR / f"lightgbm_v4_{regime}_{name}.txt"
                    if not path.exists():
                        self._boosters = {}
                        return False
                    self._boosters[(regime, name)] = lgb.Booster(model_file=str(path))
            return True
        except Exception as exc:  # noqa: BLE001 - a missing wheel must not stop the run
            log.warning("Could not load lightgbm_v4: %s", exc)
            self._boosters = {}
            return False

    def _predict(self, x: np.ndarray, lead: np.ndarray, name: str) -> np.ndarray:
        out = np.full(len(x), np.nan)
        short = np.clip(lead, 1, F3.MAX_LEAD) == F.SHORT_LEAD
        for regime, mask in (("short", short), ("long", ~short)):
            if mask.any():
                out[mask] = self._boosters[(regime, name)].predict(x[mask])
        return out

    def predict(self, features: pd.DataFrame, issued_at: datetime) -> list[ForecastPoint]:
        if not self._load():
            log.warning("lightgbm_v4 artifacts missing; falling back to shrunk_scaled")
            from .shrunk_scaled import ShrunkScaled

            return ShrunkScaled().predict(features, issued_at)

        inputs = serving_inputs(features)
        prices = inputs["prices"]
        offsets = self._offsets()
        points: list[ForecastPoint] = []
        for zone, targets, target_idx, lead in zone_targets(inputs, issued_at):
            x, shrunk, scale = F.build_matrix(
                zone, target_idx, lead, prices, inputs["weather"], inputs["fuels"], inputs["hour0"],
                inputs["holidays"], None, inputs["stats"],
            )
            level = np.where(np.isnan(shrunk), np.nanmean(prices[zone]), shrunk)
            clipped = np.clip(lead, 1, F3.MAX_LEAD)
            zone_offsets = offsets.get(zone, {})
            down = np.array([zone_offsets.get(str(int(k)), (0.0, 0.0))[0] for k in clipped])
            up = np.array([zone_offsets.get(str(int(k)), (0.0, 0.0))[1] for k in clipped])
            q50 = level + scale * self._predict(x, lead, "q50")
            q10 = level + scale * (self._predict(x, lead, "q10") - down)
            q90 = level + scale * (self._predict(x, lead, "q90") + up)
            for ts, lo, mid, hi in zip(targets["ts"], q10, q50, q90):
                p10, p50, p90 = order_quantiles(float(lo), float(mid), float(hi))
                points.append(ForecastPoint(ts=ts.to_pydatetime().astimezone(TZ), zone=zone, p10=p10, p50=p50, p90=p90))
        return points

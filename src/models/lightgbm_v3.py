"""lightgbm_v3: quantile residuals trained on forecasts of the right age.

See train_lightgbm_v3.py for what it is and how it was tested, and
lgbm3_features.py for the one feature builder both sides use. A candidate: it
is logged and scored, and becomes the default only by the rule in registry.py.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from ..timeutil import TZ
from . import lgbm3_features as F
from .base import ForecastPoint, order_quantiles, target_window

log = logging.getLogger(__name__)

ARTIFACTS_DIR = Path(__file__).resolve().parent / "artifacts"
WX_COLUMNS = {var: {p: f"wx_{var}_{p}" for p in F.POINTS} for var in ("temp", "wind", "solar")}


def _description() -> str:
    text = (
        "Efterföljare till Residual-LightGBM, som kandidat. Tränad på arkiverade "
        "väderprognoser med rätt framförhållning i stället för på vädret som det blev, "
        "med framförhållningen som egen variabel (1–7 dygn), tyskt och danskt väder, "
        "gaspris och utsläppsrätter. Tränas om varje vecka."
    )
    try:
        meta = json.loads((ARTIFACTS_DIR / "meta_v3.json").read_text(encoding="utf-8"))
        wf = meta["walk_forward"]["overall"]
        text += (
            f" Senast tränad {meta['trained_at'][:10]}. I test på de sex senaste månaderna, "
            f"med omträning före varje månad: medelfel {str(wf['mae_v3']).replace('.', ',')} EUR/MWh mot "
            f"{str(wf['mae_seasonal_naive']).replace('.', ',')} för den säsongsnaiva referensen. "
            "Testet är gjort på arkiverade prognoser, inte på skarpa – det avgörs på träffsäkerhetssidan."
        )
    except (OSError, KeyError, ValueError):
        pass
    return text


class LightGbmV3:
    id = "lightgbm_v3"
    name_sv = "LightGBM v3 (kandidat)"
    quantiles = True
    derived = False

    def __init__(self) -> None:
        self._boosters: dict[str, Any] = {}
        self.description_sv = _description()

    def _offsets(self) -> dict[int, tuple[float, float]]:
        """Per-lead widening of the band, measured in the walk-forward test."""
        try:
            meta = json.loads((ARTIFACTS_DIR / "meta_v3.json").read_text(encoding="utf-8"))
            return {int(k): (float(v[0]), float(v[1])) for k, v in meta["walk_forward"]["conformal_offsets"].items()}
        except (OSError, KeyError, ValueError, TypeError):
            return {}

    def _load(self) -> bool:
        if self._boosters:
            return True
        try:
            import lightgbm as lgb

            for name in ("q10", "q50", "q90"):
                path = ARTIFACTS_DIR / f"lightgbm_v3_{name}.txt"
                if not path.exists():
                    return False
                self._boosters[name] = lgb.Booster(model_file=str(path))
            return True
        except Exception as exc:  # noqa: BLE001 - a missing wheel must not stop the run
            log.warning("Could not load lightgbm_v3: %s", exc)
            self._boosters = {}
            return False

    def predict(self, features: pd.DataFrame, issued_at: datetime) -> list[ForecastPoint]:
        if not self._load():
            log.warning("lightgbm_v3 artifacts missing; falling back to shrunk_scaled")
            from .shrunk_scaled import ShrunkScaled

            return ShrunkScaled().predict(features, issued_at)

        from ..features.build import _swedish_holidays
        from ..fetch import fuels as fuels_store

        frame = features.sort_values(["zone", "ts"])
        times = pd.DatetimeIndex(sorted(frame["ts"].unique()))
        hour0 = F.hour_index(times[0].to_pydatetime())
        n = F.hour_index(times[-1].to_pydatetime()) - hour0 + 1

        def grid(zone_frame: pd.DataFrame, column: str) -> np.ndarray:
            out = np.full(n, np.nan)
            idx = F.hour_indices(zone_frame["ts"]) - hour0
            out[idx] = pd.to_numeric(zone_frame[column], errors="coerce").to_numpy()
            return out

        by_zone = {zone: group for zone, group in frame.groupby("zone")}
        prices = {zone: grid(by_zone[zone], "actual_price") for zone in F.ZONE_ORDER}
        any_zone = by_zone[F.ZONE_ORDER[0]]
        weather = {
            point: {var: grid(any_zone, WX_COLUMNS[var][point]).reshape(-1, 1) for var in ("temp", "wind", "solar")}
            for point in F.POINTS
        }
        fuels = F.fuel_arrays(fuels_store.load_fuels(), hour0, n)
        holidays = _swedish_holidays(sorted({t.year for t in times}))
        stats = {zone: F.daily_stats(prices[zone], hour0) for zone in F.ZONE_ORDER}

        start, end = target_window(issued_at)
        offsets = self._offsets()
        points: list[ForecastPoint] = []
        for zone in F.ZONE_ORDER:
            zf = by_zone[zone]
            targets = zf[(zf["ts"] >= pd.Timestamp(start)) & (zf["ts"] <= pd.Timestamp(end))]
            if targets.empty:
                continue
            # The last local day whose prices are all published.
            priced = zf[zf["actual_price"].notna()]
            per_day = priced.groupby(priced["ts"].dt.tz_convert(TZ).dt.date).size()
            full_days = per_day[per_day >= 23]
            last_known = full_days.index.max() if len(full_days) else (issued_at.astimezone(TZ).date() - timedelta(days=1))

            target_idx = F.hour_indices(targets["ts"]) - hour0
            target_days = targets["ts"].dt.tz_convert(TZ).dt.date
            lead = np.array([(day - last_known).days for day in target_days])

            x, shrunk = F.build_matrix(
                zone, target_idx, lead, prices, weather, fuels, hour0, holidays, None, stats
            )
            level = np.where(np.isnan(shrunk), np.nanmean(prices[zone]), shrunk)
            clipped = np.clip(lead, 1, F.MAX_LEAD)
            down = np.array([offsets.get(int(k), (0.0, 0.0))[0] for k in clipped])
            up = np.array([offsets.get(int(k), (0.0, 0.0))[1] for k in clipped])
            q50 = level + self._boosters["q50"].predict(x)
            q10 = level + self._boosters["q10"].predict(x) - down
            q90 = level + self._boosters["q90"].predict(x) + up
            for ts, lo, mid, hi in zip(targets["ts"], q10, q50, q90):
                p10, p50, p90 = order_quantiles(float(lo), float(mid), float(hi))
                points.append(ForecastPoint(ts=ts.to_pydatetime().astimezone(TZ), zone=zone, p10=p10, p50=p50, p90=p90))
        return points

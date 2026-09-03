"""Main entrypoint: fetch, forecast, score, publish.

    python -m src.pipeline

Runs to completion even when upstream sources fail — a degraded run still
publishes, and says so in api/v1/status.json.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import timedelta

from .config import EVAL_WINDOW_DAYS, FIXTURE_ACTUALS_PATH, ZONES, ensure_dirs
from .evaluate.score import evaluate, zone_slice
from .explain.drivers import build_drivers, global_blurb
from .features.build import build_features
from .fetch import entsoe_fundamentals, entsoe_prices, open_meteo, svk_text
from .models.official import actuals_index
from .models.registry import (
    BASE_MODELS,
    DEFAULT_MODEL_ID,
    DERIVED_MODELS,
    REFERENCE_MODEL_ID,
    model_ids,
)
from .publish import api as publish_api
from .publish import site_data as publish_site
from .store import (
    append_forecasts,
    load_actuals,
    load_forecasts,
    read_jsonl,
    rotate_forecasts,
    upsert_actuals,
)
from .timeutil import iso, now_local, run_id_for

log = logging.getLogger("pipeline")


def _load_demo_actuals() -> list[dict]:
    if not FIXTURE_ACTUALS_PATH.exists():
        return []
    rows = list(read_jsonl(FIXTURE_ACTUALS_PATH))
    log.warning("No official prices available — falling back to %s demo rows", len(rows))
    return rows


def run(skip_fetch: bool = False) -> int:
    ensure_dirs()
    now = now_local()
    run_id = run_id_for(now)
    log.info("Run %s starting at %s", run_id, iso(now))

    sources: dict[str, dict] = {}

    # ---- 2. fetch -------------------------------------------------------
    if skip_fetch:
        sources["entsoe_prices"] = {"ok": False, "error": "skipped (--skip-fetch)"}
        sources["open_meteo"] = {"ok": False, "error": "skipped (--skip-fetch)"}
        sources["entsoe_fundamentals"] = {"ok": False, "error": "skipped (--skip-fetch)"}
        sources["svk_text"] = {"ok": False, "error": "skipped (--skip-fetch)"}
        weather, fundamentals, svk = None, None, svk_text.load_cached_svk_text()
        price_rows: list[dict] = []
    else:
        start, end = entsoe_prices.scheduled_window(now)
        if entsoe_prices.token_available():
            price_rows, sources["entsoe_prices"] = entsoe_prices.fetch_prices(start, end)
        else:
            price_rows, sources["entsoe_prices"] = [], {
                "ok": False,
                "rows": 0,
                "error": "ENTSOE_TOKEN is not set",
            }

        weather, sources["open_meteo"] = open_meteo.fetch_weather()
        fundamentals, sources["entsoe_fundamentals"] = entsoe_fundamentals.fetch_fundamentals()
        svk, sources["svk_text"] = svk_text.fetch_svk_text()
        if svk is None:
            svk = svk_text.load_cached_svk_text()

    # ---- 3. actuals -----------------------------------------------------
    added = upsert_actuals(price_rows) if price_rows else 0
    actuals = load_actuals()
    demo = False
    if not actuals:
        actuals = _load_demo_actuals()
        demo = bool(actuals)

    degraded = not sources["entsoe_prices"]["ok"] or not sources["open_meteo"]["ok"]

    # ---- 4. features ----------------------------------------------------
    if weather is not None and not weather.empty:
        climatology = open_meteo.update_climatology(weather, now)
        weather = open_meteo.add_indices(weather, climatology)
        regional = open_meteo.regional_indices(weather)
    else:
        import pandas as pd

        weather = pd.DataFrame(columns=["ts", "point"])
        regional = pd.DataFrame(columns=["ts", "wind_index_north", "wind_index_south"])

    features = build_features(actuals, weather, regional, fundamentals, now)

    # ---- 5-7. models ----------------------------------------------------
    issued_at = now
    predictions: dict[str, list] = {}
    for model in BASE_MODELS:
        try:
            predictions[model.id] = model.predict(features, issued_at)
            log.info("%s: %s points", model.id, len(predictions[model.id]))
        except Exception as exc:  # noqa: BLE001 - one broken model must not stop the run
            log.exception("Model %s failed: %s", model.id, exc)
            sources[f"model:{model.id}"] = {"ok": False, "error": str(exc)[:200]}
            degraded = True

    for model in DERIVED_MODELS:
        try:
            predictions[model.id] = model.combine(predictions, issued_at)
            log.info("%s: %s points", model.id, len(predictions[model.id]))
        except Exception as exc:  # noqa: BLE001
            log.exception("Derived model %s failed: %s", model.id, exc)
            sources[f"model:{model.id}"] = {"ok": False, "error": str(exc)[:200]}
            degraded = True

    written = 0
    for model_id, points in predictions.items():
        rows = [p.as_row(model_id, issued_at, run_id) for p in points]
        written += append_forecasts(rows)
    log.info("Appended %s forecast rows", written)

    # ---- 8. rotation ----------------------------------------------------
    rotation = rotate_forecasts()
    if rotation:
        sources["forecast_log"] = {"ok": True, "note": rotation}

    # ---- 9. evaluation --------------------------------------------------
    forecasts = load_forecasts(since=now - timedelta(days=EVAL_WINDOW_DAYS + 1))
    accuracy = evaluate(
        forecasts, actuals, model_ids(), REFERENCE_MODEL_ID, now, DEFAULT_MODEL_ID
    )

    # ---- 10. drivers ----------------------------------------------------
    ensemble_by_zone: dict[str, list[dict]] = {zone: [] for zone in ZONES}
    for point in predictions.get("ensemble", []):
        ensemble_by_zone[point.zone].append({"ts": point.ts, "p50": point.p50})
    drivers = build_drivers(features, ensemble_by_zone, svk, now)

    # ---- 11-12. publish -------------------------------------------------
    meta = {
        "generated_at": iso(now),
        "run_id": run_id,
        "degraded": degraded,
        "demo": demo,
        "models": model_ids(),
        "now": now,
    }
    index = actuals_index(actuals)

    forecast_payloads: dict[str, dict] = {}
    zone_slices: dict[str, dict] = {}
    for zone in ZONES:
        series = publish_api.build_series(zone, predictions, index, now, demo)
        payload = publish_api.write_forecast(zone, series, drivers[zone], meta)
        history = publish_api.build_history(zone, actuals, forecasts, now)
        accuracy_slice = zone_slice(accuracy, zone)
        forecast_payloads[zone] = payload
        zone_slices[zone] = accuracy_slice
        publish_site.write_zone(zone, payload, history, accuracy_slice, now)

    publish_api.write_zones_index(forecast_payloads, meta)
    publish_api.write_models()
    publish_api.write_accuracy(accuracy, zone_slices)
    status = publish_api.write_status(run_id, sources, degraded, now, demo)

    publish_site.write_overview(
        forecast_payloads, accuracy, status, global_blurb(drivers, degraded), now
    )
    publish_site.write_accuracy(accuracy)
    publish_site.write_models()
    publish_site.write_status(status)

    # ---- 13. summary ----------------------------------------------------
    print(f"run_id={run_id}")
    print(f"  status          : {'DEGRADED' if degraded else 'ok'}{' (demo data)' if demo else ''}")
    print(f"  actuals         : {len(actuals)} rows (+{added} new)")
    print(f"  forecast rows   : +{written}")
    print(f"  scored points   : {accuracy['scored_points']}")
    for name, state in sources.items():
        flag = "ok" if state.get("ok") else f"FAIL — {state.get('error', 'unknown')}"
        print(f"  {name:<16}: {flag}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the Power Price Oracle pipeline")
    parser.add_argument(
        "--skip-fetch",
        action="store_true",
        help="Republish from stored state without calling any upstream API",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    return run(skip_fetch=args.skip_fetch)


if __name__ == "__main__":
    sys.exit(main())

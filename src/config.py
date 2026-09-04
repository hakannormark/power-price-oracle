"""Domain constants and filesystem paths for Power Price Oracle."""

from __future__ import annotations

import os
from pathlib import Path


def _load_dotenv() -> None:
    """Read a local .env into the environment, without overriding real env vars.

    Lets a developer keep ENTSOE_TOKEN out of their shell profile and out of the
    repo; CI passes the secret as a real environment variable and is untouched.
    """
    path = Path(__file__).resolve().parent.parent / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


_load_dotenv()

PROJECT_NAME = "Power Price Oracle"
SITE_TITLE_SV = "Spotprognos"
SITE_SUBTITLE_SV = "Veckoprognos för Nord Pool SE1–SE4"

TIMEZONE = "Europe/Stockholm"
HORIZON_HOURS = 168  # 7 days
RESOLUTION = "PT60M"
CURRENCY = "EUR/MWh"

# The day-ahead auction result for delivery day D is published around 12:45 local
# time on D-1. Anything at or after that instant is fact, not forecast.
AUCTION_CUTOFF_HOUR = 12
AUCTION_CUTOFF_MINUTE = 45

ZONES = {
    "SE1": {
        "name": "Luleå",
        "eic": "10Y1001A1001A44P",
        "entsoe_code": "SE_1",
        "lat": 65.5848,
        "lon": 22.1567,
    },
    "SE2": {
        "name": "Sundsvall",
        "eic": "10Y1001A1001A45N",
        "entsoe_code": "SE_2",
        "lat": 62.3908,
        "lon": 17.3069,
    },
    "SE3": {
        "name": "Stockholm",
        "eic": "10Y1001A1001A46L",
        "entsoe_code": "SE_3",
        "lat": 59.3293,
        "lon": 18.0686,
    },
    "SE4": {
        "name": "Malmö",
        "eic": "10Y1001A1001A47J",
        "entsoe_code": "SE_4",
        "lat": 55.6050,
        "lon": 13.0038,
    },
}

ZONE_IDS = list(ZONES)

# Extra weather points used as regional drivers (not shown as zones)
WEATHER_POINTS = {
    "SE3_GOT": (57.7089, 11.9746),
    "DK2": (55.6761, 12.5683),
    "DE_NORTH": (53.5511, 9.9937),
    "NO1": (59.9139, 10.7522),
    "NO2": (58.9700, 5.7331),
}

# Every weather location we fetch: the four zone capitals plus the drivers.
ALL_WEATHER_POINTS = {
    **{zid: (z["lat"], z["lon"]) for zid, z in ZONES.items()},
    **WEATHER_POINTS,
}

NORTH_WIND_POINTS = ["SE1", "SE2"]
SOUTH_WIND_POINTS = ["SE3", "SE4", "DK2"]

HORIZON_BUCKETS = [
    ("0-24h", 0, 24),
    ("24-48h", 24, 48),
    ("48-72h", 48, 72),
    ("72-96h", 72, 96),
    ("96-120h", 96, 120),
    ("120-144h", 120, 144),
    ("144-168h", 144, 168),
]
BUCKET_LABELS = [b[0] for b in HORIZON_BUCKETS]

# Evaluation
EVAL_WINDOW_DAYS = 90
MIN_SAMPLES_FOR_STATS = 24  # below this a bucket is reported as "för lite data"
MIN_SAMPLES_FOR_CORR = 24

# History available to the models (seasonal naive needs ~8 weeks of same-hour data)
FEATURE_HISTORY_DAYS = 63
HISTORY_API_DAYS = 30

# Backfill
BACKFILL_DAYS = 90
# Supply-side drivers move over seasons, so learning anything from them needs
# years of prices, not months.
DEEP_BACKFILL_DAYS = 1095
# Workflow trigger: run the backfill when actuals hold fewer than 30 days x 4 zones.
BACKFILL_MIN_ROWS = 24 * 30 * 4

# Storage rotation
FORECAST_ROTATE_MB = 80
FORECAST_RETAIN_DAYS = 180

# Endpoints
ENTSOE_API_URL = "https://web-api.tp.entsoe.eu/api"
OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
SVK_DRIFTINFO_URL = (
    "https://www.svk.se/om-kraftsystemet/kraftsystemdata/information-fran-driften/"
)
ECB_FX_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"

# Reject a EUR/SEK reading outside this band: it means the feed changed shape,
# not that the krona moved that far.
FX_SANITY_RANGE = (7.0, 20.0)

HTTP_TIMEOUT = 45
HTTP_RETRIES = 3

# Paths
ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
ARCHIVE_DIR = DATA_DIR / "archive"
FIXTURES_DIR = DATA_DIR / "fixtures"
ACTUALS_DIR = DATA_DIR / "actuals"          # one JSONL per delivery year
LEGACY_ACTUALS_PATH = DATA_DIR / "actuals.jsonl"  # migrated on first run
FORECASTS_PATH = DATA_DIR / "forecasts.jsonl"
CLIMATOLOGY_PATH = DATA_DIR / "weather_climatology.json"
FX_PATH = DATA_DIR / "fx_rate.json"
RESERVOIRS_PATH = DATA_DIR / "supply" / "reservoirs.jsonl"
SVK_TEXT_PATH = RAW_DIR / "svk_driftinfo.txt"
FIXTURE_ACTUALS_PATH = FIXTURES_DIR / "actuals_demo.jsonl"

API_DIR = ROOT / "api" / "v1"
SITE_DIR = ROOT / "site"
SITE_DATA_DIR = SITE_DIR / "data"
SITE_API_DIR = SITE_DIR / "api" / "v1"

ROUND_DECIMALS = 3

# Repository / deployment metadata surfaced in the UI footer.
REPO_URL = "https://github.com/hakannormark/power-price-oracle"


def ore_per_kwh(eur_per_mwh: float, eur_sek: float) -> float:
    """EUR/MWh -> öre/kWh, via the EUR/SEK rate.

    The market is denominated in EUR, so this is a currency conversion and not
    just a change of unit: 1 EUR/MWh is 0.1 eurocent/kWh, which only becomes öre
    after multiplying by SEK per EUR. At 11.50 SEK/EUR, 110 EUR/MWh is about
    126 öre/kWh.
    """
    return eur_per_mwh * eur_sek / 10.0


def ensure_dirs() -> None:
    for path in (DATA_DIR, RAW_DIR, API_DIR, SITE_API_DIR, SITE_DATA_DIR):
        path.mkdir(parents=True, exist_ok=True)
    for base in (API_DIR, SITE_API_DIR):
        for zone in ZONES:
            (base / "zones" / zone).mkdir(parents=True, exist_ok=True)

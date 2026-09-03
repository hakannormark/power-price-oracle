"""Small retrying HTTP helper shared by the fetch adapters."""

from __future__ import annotations

import logging
import time
from typing import Any

import requests

from ..config import HTTP_RETRIES, HTTP_TIMEOUT

log = logging.getLogger(__name__)

USER_AGENT = "PowerPriceOracle/1.0 (+https://github.com/hakannormark/power-price-oracle)"


def get(url: str, params: dict[str, Any] | None = None, retries: int = HTTP_RETRIES) -> requests.Response:
    """GET with linear backoff. Raises the last exception if all attempts fail."""
    last: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            response = requests.get(
                url,
                params=params,
                timeout=HTTP_TIMEOUT,
                headers={"User-Agent": USER_AGENT},
            )
            response.raise_for_status()
            return response
        except Exception as exc:  # noqa: BLE001 - adapters decide how to degrade
            last = exc
            log.warning("GET %s failed (attempt %s/%s): %s", url, attempt, retries, exc)
            if attempt < retries:
                time.sleep(2 * attempt)
    assert last is not None
    raise last

"""Small retrying HTTP helper shared by the fetch adapters."""

from __future__ import annotations

import logging
import time
from typing import Any

import requests

from ..config import HTTP_RETRIES, HTTP_TIMEOUT

log = logging.getLogger(__name__)

USER_AGENT = "PowerPriceOracle/1.0 (+https://github.com/hakannormark/power-price-oracle)"


def _request(
    method: str,
    url: str,
    retries: int,
    headers: dict[str, str] | None,
    **kwargs: Any,
) -> requests.Response:
    """One request with linear backoff. Raises the last exception if all attempts fail."""
    last: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            response = requests.request(
                method,
                url,
                timeout=HTTP_TIMEOUT,
                headers={"User-Agent": USER_AGENT, **(headers or {})},
                **kwargs,
            )
            response.raise_for_status()
            return response
        except Exception as exc:  # noqa: BLE001 - adapters decide how to degrade
            last = exc
            log.warning("%s %s failed (attempt %s/%s): %s", method, url, attempt, retries, exc)
            if attempt < retries:
                time.sleep(2 * attempt)
    assert last is not None
    raise last


def get(
    url: str,
    params: dict[str, Any] | None = None,
    retries: int = HTTP_RETRIES,
    headers: dict[str, str] | None = None,
) -> requests.Response:
    """GET with linear backoff. `headers` extend, and may override, the defaults."""
    return _request("GET", url, retries, headers, params=params)


def post(
    url: str,
    data: dict[str, Any] | None = None,
    retries: int = HTTP_RETRIES,
    headers: dict[str, str] | None = None,
) -> requests.Response:
    """Form POST with the same backoff as get()."""
    return _request("POST", url, retries, headers, data=data)

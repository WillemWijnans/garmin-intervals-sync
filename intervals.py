"""Intervals.icu client for pushing wellness data."""

import logging

import requests
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

logger = logging.getLogger(__name__)

BASE_URL = "https://intervals.icu/api/v1"


class IntervalsAuthError(Exception):
    """Raised on 401/403 from Intervals.icu (permanent failure)."""
    pass


def _is_retryable(exc: BaseException) -> bool:
    """Retry on network errors, 5xx, and 429. Never retry 4xx."""
    if isinstance(exc, requests.exceptions.ConnectionError):
        return True
    if isinstance(exc, requests.exceptions.Timeout):
        return True
    if isinstance(exc, requests.HTTPError) and exc.response is not None:
        status = exc.response.status_code
        return status == 429 or status >= 500
    return False


class IntervalsClient:
    """Pushes wellness data to Intervals.icu."""

    def __init__(self, athlete_id: str, api_key: str):
        self._athlete_id = athlete_id
        self._api_key = api_key
        self._session = requests.Session()
        self._session.auth = ("API_KEY", api_key)
        self._session.headers["Content-Type"] = "application/json"

    @retry(
        retry=retry_if_exception(_is_retryable),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=4),
        reraise=True,
    )
    def put_wellness(
        self, date_str: str, standard_fields: dict, custom_fields: dict | None = None
    ) -> None:
        """PUT wellness fields for a single date.

        Args:
            date_str: ISO date string (YYYY-MM-DD).
            standard_fields: Dict of {intervals_field_name: value}.
            custom_fields: Dict of {custom_field_name: value}, sent
                under the "custom" key in the PUT body.
                Skips the call entirely if both dicts are empty.

        Raises:
            IntervalsAuthError: On 401/403 (permanent, do not retry).
            requests.HTTPError: On other HTTP failures (after retries).
        """
        if not standard_fields and not custom_fields:
            logger.debug("No fields for %s, skipping PUT", date_str)
            return

        body = {**standard_fields, **(custom_fields or {})}

        url = f"{BASE_URL}/athlete/{self._athlete_id}/wellness/{date_str}"

        resp = self._session.put(url, json=body)

        if resp.status_code in (401, 403):
            raise IntervalsAuthError(
                f"Intervals.icu auth failed ({resp.status_code}): {resp.text}"
            )

        resp.raise_for_status()
        field_count = len(standard_fields) + len(custom_fields or {})
        logger.debug("PUT %s → %d (%d fields)", date_str, resp.status_code, field_count)

"""Garmin Connect client for fetching wellness data via python-garminconnect."""

import logging
import shutil
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Iterator

from garminconnect import Garmin

logger = logging.getLogger(__name__)

TOKEN_STORE = str(Path.home() / ".garminconnect")


class GarminAuthError(Exception):
    """Raised when Garmin authentication fails."""
    pass


class GarminClient:
    """Fetches wellness data from Garmin Connect."""

    def __init__(self, username: str, password: str):
        self._client = Garmin(
            username,
            password,
            prompt_mfa=lambda: input("MFA code: "),
        )
        self._last_call_time: float | None = None

    def login(self, force_reauth: bool = False) -> None:
        """Authenticate with Garmin Connect.

        The library handles token load, validation, refresh, and
        fallback to fresh login internally via login(tokenstore=...).

        Args:
            force_reauth: Clear cached tokens and force fresh login
                (needed when MFA is triggered).

        Raises:
            GarminAuthError: If authentication fails.
        """
        if force_reauth:
            token_path = Path(TOKEN_STORE)
            if token_path.exists():
                shutil.rmtree(token_path, ignore_errors=True)
                logger.info("Cleared cached tokens for re-authentication")

        try:
            self._client.login(tokenstore=TOKEN_STORE)
            logger.info("Garmin login successful (tokens cached to %s)", TOKEN_STORE)
        except Exception as e:
            raise GarminAuthError(
                f"Garmin auth failed: {e}. "
                "If MFA was triggered, run `python sync.py --reauth` interactively."
            ) from e

    def _rate_limited_call(self, method, *args, **kwargs):
        """Call a client method respecting 0.5s spacing between requests."""
        if self._last_call_time is not None:
            elapsed = time.time() - self._last_call_time
            if elapsed < 0.5:
                time.sleep(0.5 - elapsed)

        result = method(*args, **kwargs)
        self._last_call_time = time.time()
        return result

    def _extract_vo2max(self, metrics_list: list) -> float | None:
        """Extract cycling VO2 Max only.

        Cycling-only by design — running VO2 Max would pollute the cycling
        fitness trend on days I run instead of ride. If cycling is null
        (e.g., no recent ride to compute from), return None and let the
        field be skipped for that date.
        """
        if not metrics_list:
            return None
        cycling = metrics_list[0].get("cycling")
        if cycling and cycling.get("vo2MaxPreciseValue") is not None:
            return cycling["vo2MaxPreciseValue"]
        return None

    def fetch_wellness(self, day: date) -> dict:
        """Fetch wellness data for a single date.

        Merges data from 4 endpoints into a single dict suitable for
        dot-path resolution by the YAML mapping:
        - get_stats: flat top-level fields (restingHeartRate, bodyBattery, SpO2, respiration)
        - get_max_metrics: synthetic top-level 'vo2Max' field
        - get_body_composition: nested 'totalAverage' dict (weight, bmi, bodyFat)
        - get_hrv_data: nested 'hrvSummary' dict (weeklyAvg, lastNightAvg)

        Returns empty dict if no data available.
        """
        date_str = day.isoformat()
        result = {}

        # Daily stats — resting HR, body battery, SpO2, respiration (flat keys)
        try:
            stats = self._rate_limited_call(self._client.get_stats, date_str)
            if isinstance(stats, dict):
                result.update(stats)
        except Exception as e:
            logger.debug("No daily stats for %s: %s", date_str, e)

        # VO2 Max — extract and inject as synthetic top-level field
        try:
            metrics = self._rate_limited_call(self._client.get_max_metrics, date_str)
            vo2max = self._extract_vo2max(metrics)
            if vo2max is not None:
                result["vo2Max"] = vo2max
        except Exception as e:
            logger.debug("No VO2 Max data for %s: %s", date_str, e)

        # Body composition — preserve 'totalAverage' nested for dot-path resolution
        try:
            body = self._rate_limited_call(
                self._client.get_body_composition, date_str, date_str
            )
            if isinstance(body, dict) and "totalAverage" in body:
                result["totalAverage"] = body["totalAverage"]
        except Exception as e:
            logger.debug("No body composition for %s: %s", date_str, e)

        # HRV — preserve 'hrvSummary' nested for dot-path resolution
        try:
            hrv = self._rate_limited_call(self._client.get_hrv_data, date_str)
            if isinstance(hrv, dict) and "hrvSummary" in hrv:
                result["hrvSummary"] = hrv["hrvSummary"]
        except Exception as e:
            logger.debug("No HRV data for %s: %s", date_str, e)

        # Sleep — preserve 'dailySleepDTO' nested for dot-path resolution
        try:
            sleep = self._rate_limited_call(self._client.get_sleep_data, date_str)
            if isinstance(sleep, dict) and "dailySleepDTO" in sleep:
                result["dailySleepDTO"] = sleep["dailySleepDTO"]
        except Exception as e:
            logger.debug("No sleep data for %s: %s", date_str, e)

        # Training Readiness — wake-up score (last entry = earliest = morning)
        try:
            readiness = self._rate_limited_call(
                self._client.get_training_readiness, date_str
            )
            if isinstance(readiness, list) and readiness:
                result["trainingReadiness"] = readiness[-1]["score"]
        except Exception as e:
            logger.debug("No training readiness for %s: %s", date_str, e)

        logger.debug("Fetched %d top-level fields for %s", len(result), date_str)
        return result

    def fetch_wellness_range(
        self, start_date: date, end_date: date
    ) -> Iterator[tuple[date, dict]]:
        """Fetch wellness data for a date range (inclusive).

        Yields (date, data) tuples. Rate limiting (0.5s between calls)
        is handled internally by _rate_limited_call.
        """
        current = start_date
        while current <= end_date:
            data = self.fetch_wellness(current)
            yield current, data
            current += timedelta(days=1)

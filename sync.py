#!/usr/bin/env python3
"""Sync wellness data from Garmin Connect to Intervals.icu."""

import argparse
import logging
import sys
from datetime import date, timedelta
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

import os

import yaml
from dotenv import load_dotenv

from garmin import GarminAuthError, GarminClient
from intervals import IntervalsAuthError, IntervalsClient

LOG_DIR = Path.home() / "Library" / "Logs" / "garmin-intervals-sync"
LOG_FILE = LOG_DIR / "sync.log"
CONFIG_PATH = Path(__file__).parent / "config" / "wellness_mapping.yaml"

CONVERTERS = {
    "grams_to_kg": lambda v: round(v / 1000, 2),
    "ms_to_seconds": lambda v: round(v / 1000, 1),
}


def setup_logging(verbose: bool = False) -> None:
    """Configure logging to file (rotating) and stdout."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)

    file_handler = TimedRotatingFileHandler(
        LOG_FILE, when="midnight", backupCount=30, encoding="utf-8"
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s — %(message)s")
    )

    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setLevel(logging.DEBUG if verbose else logging.INFO)
    stdout_handler.setFormatter(
        logging.Formatter("%(levelname)s %(message)s")
    )

    root.addHandler(file_handler)
    root.addHandler(stdout_handler)


def load_config() -> list[dict]:
    """Load wellness field mappings from YAML.

    Raises:
        SystemExit: If config file is missing or invalid.
    """
    if not CONFIG_PATH.exists():
        logging.error("Config file not found: %s", CONFIG_PATH)
        sys.exit(1)

    try:
        with open(CONFIG_PATH) as f:
            config = yaml.safe_load(f)
        mappings = config["mappings"]
        if not isinstance(mappings, list):
            raise ValueError("'mappings' must be a list")
        return mappings
    except Exception as e:
        logging.error("Invalid config file %s: %s", CONFIG_PATH, e)
        sys.exit(1)


def load_credentials() -> dict:
    """Load credentials from .env file (primary) or Keychain (fallback).

    Raises:
        SystemExit: If any credential is missing from both sources.
    """
    load_dotenv()
    creds = {
        "garmin_username": os.getenv("GARMIN_USERNAME"),
        "garmin_password": os.getenv("GARMIN_PASSWORD"),
        "intervals_api_key": os.getenv("INTERVALS_API_KEY"),
        "intervals_athlete_id": os.getenv("INTERVALS_ATHLETE_ID"),
    }

    # Fallback to keyring for any missing values (Mac backward compat)
    try:
        import keyring
        if not creds["garmin_username"]:
            creds["garmin_username"] = keyring.get_password("garmin", "username")
        if not creds["garmin_password"]:
            creds["garmin_password"] = keyring.get_password("garmin", "password")
        if not creds["intervals_api_key"]:
            creds["intervals_api_key"] = keyring.get_password("intervals", "api_key")
        if not creds["intervals_athlete_id"]:
            creds["intervals_athlete_id"] = keyring.get_password("intervals", "athlete_id")
    except Exception:
        pass

    missing = [k for k, v in creds.items() if not v]
    if missing:
        logging.error(
            "Missing credentials: %s. Set them in .env or Keychain. See README.",
            ", ".join(missing),
        )
        sys.exit(1)

    return creds


def resolve_value(data: dict, dot_path: str):
    """Walk a dot-separated path into a nested dict.

    Returns None if any segment is missing.
    """
    current = data
    for key in dot_path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(key)
        if current is None:
            return None
    return current


def apply_mapping(garmin_data: dict, mappings: list[dict]) -> dict:
    """Map raw Garmin fields to Intervals.icu field names.

    Returns {"standard": {...}, "custom": {...}} with resolved values
    split by whether the mapping entry has custom: true.

    Skips fields where the Garmin value is None or missing.
    Applies unit conversions per mapping entry.
    Logs a warning if a mapping references a Garmin field that
    doesn't exist at all (likely a config typo).
    """
    logger = logging.getLogger(__name__)
    standard = {}
    custom = {}

    for entry in mappings:
        garmin_path = entry["garmin_path"]
        intervals_field = entry["intervals_field"]
        convert = entry.get("convert")
        is_custom = entry.get("custom", False)

        value = resolve_value(garmin_data, garmin_path)

        if value is None:
            # Only warn for top-level keys missing entirely — nested misses
            # (e.g., hrv.weeklyAvg when hrv exists but weeklyAvg doesn't)
            # are normal and not worth warning about.
            top_key = garmin_path.split(".")[0]
            if top_key in garmin_data:
                logger.debug("Field %s resolved to None, skipping", garmin_path)
            else:
                logger.warning(
                    "Garmin field '%s' not found in response — check mapping config",
                    garmin_path,
                )
            continue

        if convert:
            converter = CONVERTERS.get(convert)
            if converter is None:
                logger.warning("Unknown converter '%s' for %s, skipping", convert, garmin_path)
                continue
            value = converter(value)

        if is_custom:
            custom[intervals_field] = value
        else:
            standard[intervals_field] = value

    return {"standard": standard, "custom": custom}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Sync Garmin wellness data to Intervals.icu"
    )
    parser.add_argument(
        "--reauth", action="store_true",
        help="Force interactive Garmin re-login (for MFA)",
    )
    parser.add_argument(
        "--verbose", action="store_true",
        help="Enable DEBUG logging (per-field detail)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Fetch and map data but skip Intervals PUT",
    )

    # Date range: --days (rolling window) vs --start/--end (explicit range)
    range_group = parser.add_mutually_exclusive_group()
    range_group.add_argument(
        "--days", type=int, default=7,
        help="Number of days to sync (default: 7, ending yesterday)",
    )
    range_group.add_argument(
        "--start", type=date.fromisoformat, default=None,
        help="Start date for targeted backfill (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--end", type=date.fromisoformat, default=None,
        help="End date for targeted backfill (YYYY-MM-DD, defaults to yesterday)",
    )
    parser.add_argument(
        "--include-today", action="store_true",
        help="Include today in the sync window (use only after morning data has settled in Garmin)",
    )
    args = parser.parse_args()

    setup_logging(verbose=args.verbose)
    logger = logging.getLogger(__name__)

    # --- Load config and credentials (fatal on failure) ---
    mappings = load_config()
    creds = load_credentials()

    # --- Initialize clients ---
    garmin = GarminClient(creds["garmin_username"], creds["garmin_password"])
    intervals = IntervalsClient(creds["intervals_athlete_id"], creds["intervals_api_key"])

    # --- Authenticate Garmin (fatal on failure) ---
    try:
        garmin.login(force_reauth=args.reauth)
    except GarminAuthError as e:
        logger.error(str(e))
        sys.exit(1)

    # --- Compute date range ---
    today = date.today()
    yesterday = today - timedelta(days=1)
    max_allowed = today if args.include_today else yesterday

    if args.end and not args.start:
        logger.error("--end requires --start")
        sys.exit(1)

    if args.start:
        start_date = args.start
        end_date = args.end if args.end else max_allowed
    else:
        end_date = max_allowed
        start_date = end_date - timedelta(days=args.days - 1)

    if start_date > end_date:
        logger.error("--start (%s) is after --end (%s)", start_date, end_date)
        sys.exit(1)

    if end_date > max_allowed:
        if args.include_today:
            logger.error("Cannot sync future dates")
        else:
            logger.error("Cannot sync today or future dates without --include-today")
        sys.exit(1)

    num_days = (end_date - start_date).days + 1

    dates_succeeded = 0
    dates_failed = 0
    total_fields = 0

    logger.info("Syncing %d days: %s → %s", num_days, start_date, end_date)

    for day, garmin_data in garmin.fetch_wellness_range(start_date, end_date):
        try:
            mapped = apply_mapping(garmin_data, mappings)
            standard = mapped["standard"]
            custom = mapped["custom"]
            field_count = len(standard) + len(custom)
            if args.dry_run:
                logger.info("DRY RUN: would have PUT %d fields for %s", field_count, day)
                logger.debug("  standard: %s", standard)
                logger.debug("  custom: %s", custom)
            else:
                intervals.put_wellness(day.isoformat(), standard, custom)
            dates_succeeded += 1
            total_fields += field_count
            logger.debug("%s: synced %d fields", day, field_count)
        except IntervalsAuthError as e:
            # Fatal — don't continue, auth won't fix itself
            logger.error(str(e))
            sys.exit(1)
        except Exception as e:
            # Per-date error — log and continue
            dates_failed += 1
            logger.error("Failed to sync %s: %s", day, e)

    # --- Summary ---
    logger.info(
        "Done: %d/%d days synced, %d fields total",
        dates_succeeded, num_days, total_fields,
    )

    if dates_succeeded == 0:
        logger.error("All dates failed — check logs at %s", LOG_FILE)
        sys.exit(1)


if __name__ == "__main__":
    main()

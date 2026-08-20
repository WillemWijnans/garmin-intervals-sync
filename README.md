# Garmin → Intervals.icu Wellness Sync

Syncs wellness data from Garmin Connect to [Intervals.icu](https://intervals.icu) — fields that Intervals' built-in Garmin sync doesn't cover: SpO2, respiration rate, VO2 Max, Body Battery (→ readiness), body fat, BMI, and calories consumed. Runs as a daily cron job with a rolling 7-day window. All writes are idempotent PUTs, so re-running is always safe.

> **Heads up:** This relies on [python-garminconnect](https://github.com/cyberjunky/python-garminconnect), which uses Garmin's **unofficial** (undocumented) API. Garmin can change it without notice, which may break syncing until the library is updated. See [Troubleshooting](#troubleshooting) if that happens. Use at your own risk — this is not affiliated with or endorsed by Garmin or Intervals.icu.

## Prerequisites

- **Python 3.12+** (check with `python3 --version`)
- **macOS or Linux**
- **Garmin Connect account** with wellness data
- **Intervals.icu account** with an API key ([Settings → Developer](https://intervals.icu/settings))

## Installation

```bash
git clone https://github.com/WillemWijnans/garmin-intervals-sync.git ~/code/garmin-intervals-sync
cd ~/code/garmin-intervals-sync
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Credential Setup

Credentials can be provided via `.env` file (recommended, works everywhere) or macOS Keychain (fallback). The script checks `.env` first, then Keychain for any missing values.

### Option A: .env file (Linux, VPS, or Mac)

```bash
cp .env.example .env
# Edit .env with your actual credentials
```

The `.env` file is gitignored and never committed.

### Option B: macOS Keychain (Mac only, backward compatible)

```bash
python3 -c "import keyring; keyring.set_password('garmin', 'username', 'your-garmin-email@example.com')"
python3 -c "import keyring; keyring.set_password('garmin', 'password', 'your-garmin-password')"
python3 -c "import keyring; keyring.set_password('intervals', 'api_key', 'your-intervals-api-key')"
python3 -c "import keyring; keyring.set_password('intervals', 'athlete_id', 'i12345')"
```

## First Run

Run interactively to handle Garmin MFA and verify the field mapping:

```bash
python sync.py --reauth --dry-run --verbose
```

- `--reauth` — forces a fresh Garmin login (prompts for MFA code if Garmin challenges)
- `--dry-run` — fetches and maps data but skips writing to Intervals, so you can inspect the output
- `--verbose` — shows per-field DEBUG detail

You should see lines like `DRY RUN: would have PUT 8 fields for 2026-05-03`. Once that looks right, run for real:

```bash
python sync.py --verbose
```

## Cron Setup

After a successful manual run, set up a daily cron job. Cron has a minimal PATH, so use absolute paths to the venv Python and the script.

Find your venv Python path:

```bash
source .venv/bin/activate
which python
# e.g. /Users/yourname/code/garmin-intervals-sync/.venv/bin/python
```

Add the cron entry:

```bash
crontab -e
```

```cron
0 9 * * * /Users/yourname/code/garmin-intervals-sync/.venv/bin/python /Users/yourname/code/garmin-intervals-sync/sync.py >> /tmp/garmin-sync-cron.log 2>&1
```

The script logs to `~/Library/Logs/garmin-intervals-sync/sync.log` (daily rotation, 30 days kept). The cron redirect to `/tmp/garmin-sync-cron.log` captures stdout/stderr as a fallback. Non-zero exit codes will trigger cron email if your system is configured for it.

## Adding / Removing Fields

Edit `config/wellness_mapping.yaml`. Each entry maps a Garmin field to an Intervals.icu wellness field:

```yaml
- garmin_path: averageSpo2          # dot notation for nested fields (e.g. hrv.weeklyAvg)
  intervals_field: spO2             # Intervals.icu wellness API field name
  convert: grams_to_kg              # optional: grams_to_kg, ms_to_seconds, or omit
```

After editing, verify your changes:

```bash
python sync.py --dry-run --verbose
```

Check the DEBUG output to confirm new fields appear in the mapping. If a field shows a warning like `Garmin field 'foo' not found in response`, the `garmin_path` doesn't match what Garmin actually returns — run `--verbose` and inspect the raw data to find the correct key name.

## Troubleshooting

**Targeted backfill**
Use `--start` and `--end` to sync a specific date range (e.g., after first setup or recovering from a gap):

```bash
python sync.py --verbose --start 2024-09-01 --end 2025-08-31
```

**Manual sync including today**
By default the script never touches today's data (Garmin's metrics are still being computed mid-day). If you want to manually sync today after morning data has settled in Garmin Connect (typically by 9-10am for sleep/HRV/readiness):

```bash
python sync.py --verbose --days 1 --include-today
```

**"Garmin auth failed"**
Run `python sync.py --reauth` interactively. Garmin may have triggered MFA, which requires a terminal prompt. EU/Netherlands accounts are challenged more frequently.

**Logs**
- Main log: `~/Library/Logs/garmin-intervals-sync/sync.log` (30 days retained)
- Cron log: `/tmp/garmin-sync-cron.log`

**Field not appearing in Intervals**
Run `python sync.py --dry-run --verbose` and check the output. If the field is missing from the mapping result, either:
- Garmin didn't return data for that field on that date (normal — e.g., no SpO2 on rest days)
- The `garmin_path` in `wellness_mapping.yaml` doesn't match the actual Garmin API response key

**Library breaks after Garmin API change**
This script uses [python-garminconnect](https://github.com/cyberjunky/python-garminconnect). If Garmin changes their API or auth flow, update the library:

```bash
source .venv/bin/activate
pip install --upgrade garminconnect
```

Watch the [python-garminconnect GitHub](https://github.com/cyberjunky/python-garminconnect) for release notes and breaking changes.

## Project Structure

```
garmin-intervals-sync/
├── sync.py                        # Entry point + field mapping logic
├── garmin.py                      # Garmin Connect client (fetch wellness data)
├── intervals.py                   # Intervals.icu client (PUT wellness data)
├── config/
│   └── wellness_mapping.yaml      # Garmin → Intervals field mapping (single source of truth)
├── .env.example                   # Credential template (copy to .env)
├── .gitignore
├── requirements.txt
├── LICENSE                         # MIT
└── README.md
```

"""apply_mapping: Garmin fields -> intervals wellness fields."""
from pathlib import Path
import sys
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import sync as S

MAPPINGS = yaml.safe_load((Path(S.__file__).parent / "config" / "wellness_mapping.yaml").read_text())["mappings"]


def test_average_stress_is_synced_as_a_custom_field():
    out = S.apply_mapping({"averageStressLevel": 36}, MAPPINGS)
    assert out["custom"]["GarminAvgStress"] == 36
    assert "GarminAvgStress" not in out["standard"]


def test_zero_stress_is_a_real_reading_and_is_kept():
    assert S.apply_mapping({"averageStressLevel": 0}, MAPPINGS)["custom"]["GarminAvgStress"] == 0


def test_garmin_not_enough_data_sentinels_are_never_stored():
    for sentinel in (-1, -2):
        out = S.apply_mapping({"averageStressLevel": sentinel}, MAPPINGS)
        assert "GarminAvgStress" not in out["custom"], sentinel


def test_missing_stress_is_skipped():
    assert "GarminAvgStress" not in S.apply_mapping({"restingHeartRate": 44}, MAPPINGS)["custom"]


def test_existing_fields_still_map():
    out = S.apply_mapping({"restingHeartRate": 44, "hrvSummary": {"lastNightAvg": 51, "lastNight5MinHigh": 79},
                           "averageStressLevel": 30}, MAPPINGS)
    assert out["standard"]["restingHR"] == 44 and out["standard"]["hrv"] == 51
    assert out["custom"] == {"HrvLastNight5MinHigh": 79, "GarminAvgStress": 30}


def test_converter_rejecting_a_value_skips_the_field():
    m = [{"garmin_path": "x", "intervals_field": "X", "custom": True, "convert": "nonnegative_or_none"}]
    assert S.apply_mapping({"x": -1}, m) == {"standard": {}, "custom": {}}


def test_other_converters_unchanged():
    assert S.CONVERTERS["grams_to_kg"](78500) == 78.5
    assert S.CONVERTERS["ms_to_seconds"](1500) == 1.5

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from amt205_validator.configuration import (
    load_config,
    save_config,
    session_config,
    session_preferences,
    update_session_preferences,
)


ROOT = Path(__file__).resolve().parents[1]


class ConfigurationTests(unittest.TestCase):
    def test_approved_internal_stability_rule_is_active(self) -> None:
        config = load_config()
        self.assertTrue(config["stability"]["window_approved"])
        self.assertEqual(config["stability"]["window_s"], 120.0)
        self.assertEqual(config["stability"]["maximum_abs_slope_c_per_min"], 0.2)
        self.assertEqual(config["application"]["version"], "0.9.3")
        self.assertEqual(config["schema_version"], 7)
        self.assertEqual(config["test_scope"]["required_fan_mode"], "SESSION")
        self.assertEqual(config["test_scope"]["historical_reference_fan_mode"], "MAX")
        self.assertEqual(config["test_scope"]["injection_frequency_hz"], 50.0)
        self.assertIn("current_75", config["profiles"])
        self.assertIn("current_100", config["profiles"])
        self.assertTrue(config["initial_baseline"]["enabled"])
        self.assertEqual(config["initial_baseline"]["minimum_observation_s"], 120.0)
        self.assertEqual(config["initial_temperature_c"]["max"], 25.0)
        self.assertEqual(config["initial_temperature_c"]["normal"], 33.0)
        self.assertTrue(config["thermal_recovery"]["enabled"])
        self.assertEqual(config["thermal_recovery"]["hold_s"], 120.0)
        self.assertEqual(config["fan_mode"]["max_power_historical_time_multiplier"], 1.0)

    def test_legacy_three_times_multiplier_is_migrated_to_one(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            application = Path(temporary)
            (application / "config").mkdir()
            default = json.loads((ROOT / "config" / "default.json").read_text(encoding="utf-8"))
            (application / "config" / "default.json").write_text(json.dumps(default), encoding="utf-8")
            legacy = {
                "schema_version": 5,
                "fan_mode": {"max_power_historical_time_multiplier": 3.0},
            }
            (application / "config" / "user.json").write_text(json.dumps(legacy), encoding="utf-8")
            with patch("amt205_validator.configuration.application_root", return_value=application):
                restored = load_config()
        self.assertEqual(restored["fan_mode"]["max_power_historical_time_multiplier"], 1.0)

    def test_session_preferences_round_trip(self) -> None:
        config = json.loads((ROOT / "config" / "default.json").read_text(encoding="utf-8"))
        expected = {
            "source_mode": "Live Monitor",
            "profile": "current_25",
            "serial_number": "402101941",
            "ambient_c": "24",
            "repetition": "2",
            "output_location": r"C:\Tests\AMT205",
            "smart_delay_enabled": True,
            "smart_delay_min_c": "45.00",
            "smart_delay_max_c": "55.00",
            "maximum_temperature_setting_c": "60.00",
            "earth_enabled": True,
            "fan_mode_max_power": True,
            "compare_feedback": True,
        }
        update_session_preferences(config, expected)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "user.json"
            save_config(config, path)
            restored = session_preferences(load_config(path))
        self.assertEqual(restored, expected)

    def test_saved_normal_fan_preference_is_preserved(self) -> None:
        config = json.loads((ROOT / "config" / "default.json").read_text(encoding="utf-8"))
        update_session_preferences(config, {"fan_mode_max_power": False})
        self.assertFalse(session_preferences(config)["fan_mode_max_power"])

    def test_user_preferences_are_not_copied_into_session_evidence(self) -> None:
        config = json.loads((ROOT / "config" / "default.json").read_text(encoding="utf-8"))
        update_session_preferences(config, {"serial_number": "402101941"})
        self.assertNotIn("user_preferences", session_config(config))

    def test_invalid_saved_choices_fall_back_to_defaults(self) -> None:
        config = json.loads((ROOT / "config" / "default.json").read_text(encoding="utf-8"))
        config["user_preferences"] = {"last_session": {"source_mode": "Unknown", "profile": "missing", "repetition": "9"}}
        restored = session_preferences(config)
        self.assertEqual(restored["source_mode"], "PCAP File")
        self.assertEqual(restored["profile"], "current_50")
        self.assertEqual(restored["repetition"], "1")


if __name__ == "__main__":
    unittest.main()

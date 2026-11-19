from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

from amt205_validator.consolidation import build_compilation_payload, discover_session_folders, read_session_summary


class SessionCompilationTests(unittest.TestCase):
    def _session(self, root: Path, name: str, profile: str, repetition: int, duration: float) -> Path:
        session_dir = root / name
        evidence = session_dir / "evidence"
        evidence.mkdir(parents=True)
        metadata = {
            "generated_at": f"2026-08-27T12:0{repetition}:00-03:00",
            "session": {
                "profile": profile,
                "fan_mode": "MAX",
                "serial_number": "402101941",
                "repetition": repetition,
                "ambient_c": 23.0,
                "injection_frequency_hz": 50.0,
            },
            "configuration": {
                "profiles": {
                    profile: {
                        "label": f"Current — Power Control {profile.split('_')[-1]}%",
                        "historical_time_s": 50.0,
                    }
                },
                "fan_mode": {"max_power_historical_time_multiplier": 3.0},
            },
            "capture": {
                "first_timestamp": f"2026-08-27T12:0{repetition}:00-03:00",
                "last_timestamp": f"2026-08-27T12:0{repetition}:30-03:00",
            },
            "evaluation": {
                "elapsed_s": duration,
                "max_control_c": 61.0,
                "thermal_status": "PASS",
                "electrical_status": "NO ERROR OBSERVED",
                "status": "PASS",
            },
            "workflow": {
                "initial_baseline": {"temperature_c": 25.0},
                "thermal_recovery": {
                    "target_c": 25.0,
                    "time_to_target_s": 45.0,
                    "end_temperature_c": 25.5,
                    "reached": True,
                    "timed_out": False,
                },
            },
        }
        (evidence / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
        with (evidence / "readings.csv").open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=("profile_elapsed_s", "executing", "hottest_control_c", "hottest_internal_c"), delimiter=";")
            writer.writeheader()
            writer.writerows([
                {"profile_elapsed_s": 0, "executing": 1, "hottest_control_c": 25, "hottest_internal_c": 28},
                {"profile_elapsed_s": duration / 2, "executing": 1, "hottest_control_c": 42, "hottest_internal_c": 45},
                {"profile_elapsed_s": duration, "executing": 1, "hottest_control_c": 61, "hottest_internal_c": 58},
            ])
        return session_dir

    def test_discovers_only_complete_session_bundles(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            expected = self._session(root, "AMT205_1", "current_50", 1, 80.0)
            (root / "incomplete" / "evidence").mkdir(parents=True)
            (root / "incomplete" / "evidence" / "metadata.json").write_text("{}", encoding="utf-8")
            self.assertEqual(discover_session_folders(root), [expected.resolve()])

    def test_builds_summary_and_common_chart_grid(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = self._session(root, "AMT205_1", "current_50", 1, 80.0)
            second = self._session(root, "AMT205_2", "current_75", 2, 60.0)
            summary = read_session_summary(first)
            self.assertEqual(summary["profile_short"], "Current 50%")
            self.assertEqual(summary["margin_percent"], 60.0)
            self.assertEqual(summary["reference_s"], 50.0)
            self.assertEqual(summary["quantitative_result"], "PASS")
            payload = build_compilation_payload([first, second], root)
            self.assertEqual(len(payload["sessions"]), 2)
            self.assertEqual(len(payload["chart"]["series"]), 2)
            self.assertLessEqual(payload["chart"]["times"][-1], 80.0)
            self.assertLess(80.0 - payload["chart"]["times"][-1], 3.0)
            self.assertIsNone(payload["chart"]["series"][1]["values"][-1])
            self.assertEqual(len(payload["readings"]), 6)
            self.assertEqual(payload["sessions"][0]["cycle_number"], 1)
            self.assertEqual(payload["sessions"][1]["cycle_number"], 2)
            self.assertEqual(payload["sessions"][1]["interval_since_previous_s"], 30.0)
            self.assertEqual(payload["sessions"][0]["recovery_result"], "REACHED")

    def test_non_destructive_fan_mode_correction_is_applied_to_compilation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            session = self._session(root, "AMT205_legacy", "current_50", 1, 80.0)
            metadata_path = session / "evidence" / "metadata.json"
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            metadata["session"]["fan_mode"] = "NORMAL"
            metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
            correction = {
                "schema_version": 1,
                "corrections": {
                    "fan_mode": {
                        "recorded": "NORMAL",
                        "effective": "MAX",
                        "reason": "Confirmed by packet review.",
                    }
                },
            }
            (session / "evidence" / "corrections.json").write_text(json.dumps(correction), encoding="utf-8")
            summary = read_session_summary(session)
            self.assertEqual(summary["fan_mode"], "Max Power (corrected)")
            self.assertEqual(summary["recorded_fan_mode"], "Normal")
            self.assertTrue(summary["fan_mode_corrected"])
            self.assertEqual(summary["quantitative_result"], "PASS")

    def test_non_destructive_profile_correction_recalculates_reference(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            session = self._session(root, "AMT205_profile", "current_75", 2, 71.534)
            metadata_path = session / "evidence" / "metadata.json"
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            metadata["configuration"]["profiles"]["current_100"] = {
                "label": "Current — 1×128 A / 60 A / Power Control 100%",
                "historical_time_s": 57.49,
            }
            metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
            correction = {
                "schema_version": 1,
                "corrections": {
                    "profile": {
                        "recorded": "current_75",
                        "effective": "current_100",
                        "reason": "Operator confirmed the applied profile after the session.",
                    }
                },
            }
            (session / "evidence" / "corrections.json").write_text(json.dumps(correction), encoding="utf-8")
            summary = read_session_summary(session)
            self.assertEqual(summary["profile_key"], "current_100")
            self.assertEqual(summary["recorded_profile_key"], "current_75")
            self.assertEqual(summary["profile_short"], "Current 100% (corrected)")
            self.assertTrue(summary["profile_corrected"])
            self.assertEqual(summary["reference_s"], 57.49)
            self.assertAlmostEqual(summary["margin_percent"], 24.428596, places=6)
            self.assertEqual(summary["quantitative_result"], "PASS")


if __name__ == "__main__":
    unittest.main()

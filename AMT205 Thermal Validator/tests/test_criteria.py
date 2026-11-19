from __future__ import annotations

import json
import unittest
from pathlib import Path

from amt205_validator.criteria import RollingStability, analyze_stability, evaluate
from amt205_validator.protocol import StatusReading


ROOT = Path(__file__).resolve().parents[1]


def reading(elapsed: float | None, progress: int | None, hottest: float, *, executing: bool = True,
            error_latched: bool = False, error_code: int = 0) -> StatusReading:
    values = {name: hottest for name in ("V1 Board 1", "V2 Board 1", "V1 Board 2", "V2 Board 2", "I1 Board 1", "I2 Board 1", "I1 Board 2", "I2 Board 2", "Main Board 1", "Main Board 2")}
    return StatusReading(1, 0, 0, "", values, "V1 Board 1", hottest, "Main Board 1", hottest, executing, error_latched, error_code, progress, elapsed)


class CriteriaTests(unittest.TestCase):
    def test_non_monitoring_profile_without_injection_is_inconclusive_for_missing_injection(self) -> None:
        result = evaluate([reading(None, None, 31.5, executing=False)], "current_25", self.config, False, True)
        self.assertEqual(result.status, "INCONCLUSIVE")
        self.assertIn("No injection was observed", result.reason)

    @classmethod
    def setUpClass(cls) -> None:
        cls.config = json.loads((ROOT / "config" / "default.json").read_text(encoding="utf-8"))

    def test_historical_time_fails_early_thermal_event(self) -> None:
        result = evaluate([reading(120.0, 14, 60.0, error_latched=True, error_code=6)], "current_50", self.config, False)
        self.assertEqual(result.status, "FAIL")

    def test_historical_time_passes_at_reference(self) -> None:
        result = evaluate([reading(179.4, 6, 60.0)], "current_50", self.config, False)
        self.assertEqual(result.status, "PASS")

    def test_invalid_initial_condition_makes_result_inconclusive(self) -> None:
        result = evaluate([reading(120.0, 14, 60.0, error_latched=True, error_code=6)], "current_50", self.config, False, initial_valid=False)
        self.assertEqual(result.status, "INCONCLUSIVE")

    def test_ia1_other_is_electrical_review_and_not_thermal_stability(self) -> None:
        result = evaluate([reading(264.0, 14, 51.8, error_latched=True, error_code=0)], "current_25", self.config, False)
        self.assertEqual(result.status, "REVIEW")
        self.assertEqual(result.thermal_status, "INCONCLUSIVE")
        self.assertEqual(result.electrical_status, "REVIEW")
        self.assertIn("IA1 Other", result.reason)

    def test_latched_error_immediately_after_status_14_is_associated(self) -> None:
        termination = reading(264.0, 14, 51.8)
        termination.relative = 589.5
        latched = reading(None, None, 51.8, error_latched=True, error_code=0)
        latched.relative = 589.8
        result = evaluate([termination, latched], "current_25", self.config, False)
        self.assertEqual(result.status, "REVIEW")

    def test_unlatched_status_14_is_the_confirmed_ia1_endpoint(self) -> None:
        result = evaluate([reading(264.0, 14, 51.8)], "current_25", self.config, False)
        self.assertEqual(result.status, "REVIEW")
        self.assertIn("IA1 Other", result.reason)

    def test_ia1_endpoint_does_not_replace_the_thermal_time_reference(self) -> None:
        early = evaluate([reading(120.0, 14, 51.8, error_latched=True)], "current_50", self.config, False)
        late = evaluate([reading(180.0, 14, 51.8, error_latched=True)], "current_50", self.config, False)
        self.assertEqual(early.status, "REVIEW")
        self.assertEqual(early.thermal_status, "INCONCLUSIVE")
        self.assertEqual(late.status, "REVIEW")
        self.assertEqual(late.thermal_status, "PASS")

    def test_historical_stress_endpoint_can_be_disabled(self) -> None:
        config = json.loads(json.dumps(self.config))
        config["event_rules"]["status_14_is_historical_stress_endpoint"] = False
        result = evaluate([reading(180.0, 14, 51.8, error_latched=True)], "current_50", config, False)
        self.assertEqual(result.status, "REVIEW")
        self.assertEqual(result.thermal_status, "PASS")

    def test_free_profile_monitors_without_acceptance(self) -> None:
        result = evaluate([reading(10.0, 6, 40.0)], "custom", self.config, False, initial_valid=False)
        self.assertEqual(result.status, "MONITORING")

    def test_stability_requires_complete_window(self) -> None:
        stability = RollingStability(300, 1)
        stability.add(0, 25.0)
        stability.add(299.999, 25.5)
        self.assertFalse(stability.stable)
        stability.add(300.0, 25.5)
        self.assertTrue(stability.stable)

    def test_stability_allows_irregular_packet_spacing(self) -> None:
        stability = RollingStability(60, 1)
        stability.add(0.0, 25.0)
        stability.add(30.2, 25.2)
        stability.add(60.5, 25.4)
        self.assertTrue(stability.stable)

    def test_stability_rejects_excessive_temperature_trend(self) -> None:
        stability = RollingStability(60, 1, 0.2)
        stability.add(0.0, 25.0)
        stability.add(30.0, 25.25)
        stability.add(60.0, 25.5)
        self.assertLessEqual(stability.range_c or 0, 1.0)
        self.assertFalse(stability.stable)

    def test_unapproved_stability_window_cannot_pass_temperature_profile(self) -> None:
        config = json.loads(json.dumps(self.config))
        config["stability"]["window_approved"] = False
        config["stability"]["window_s"] = None
        result = evaluate([reading(120.0, 6, 51.0)], "current_25", config, True)
        self.assertEqual(result.status, "INCONCLUSIVE")
        self.assertIn("No injection-stabilization rule", result.reason)

    def test_current_25_passes_only_after_approved_stability(self) -> None:
        result = evaluate([reading(120.0, 6, 51.0)], "current_25", self.config, True)
        self.assertEqual(result.status, "PASS")
        self.assertEqual(result.thermal_status, "PASS")
        self.assertEqual(result.electrical_status, "NO ERROR OBSERVED")

    def test_current_75_and_100_use_legacy_column_references(self) -> None:
        current_75 = evaluate([reading(89.93, 6, 50.0)], "current_75", self.config, False)
        current_100 = evaluate([reading(57.49, 6, 50.0)], "current_100", self.config, False)
        self.assertEqual(current_75.thermal_status, "PASS")
        self.assertEqual(current_100.thermal_status, "PASS")

    def test_fan_max_power_uses_the_reference_recorded_in_max_power(self) -> None:
        pass_at_reference = evaluate([reading(179.4, 6, 50.0)], "current_50", self.config, False, fan_mode="MAX")
        self.assertEqual(pass_at_reference.thermal_status, "PASS")

    def test_fan_normal_is_comparative_even_when_max_power_time_is_reached(self) -> None:
        result = evaluate([reading(300.0, 6, 50.0)], "current_50", self.config, False, fan_mode="NORMAL")
        self.assertEqual(result.thermal_status, "INCONCLUSIVE")
        self.assertIn("comparative route", result.thermal_reason)

    def test_candidate_windows_are_diagnostic_only(self) -> None:
        samples = [reading(float(second), 6, 50.0 + (0.2 if second % 2 else 0.0)) for second in range(0, 122)]
        for index, sample in enumerate(samples):
            sample.relative = float(index)
        diagnostics = analyze_stability(samples, (30, 60, 120), 1.0)
        self.assertEqual([item.met for item in diagnostics], [True, True, True])
        self.assertEqual(diagnostics[0].first_met_elapsed_s, 30.0)
        self.assertLessEqual(diagnostics[2].minimum_range_c, 1.0)


if __name__ == "__main__":
    unittest.main()

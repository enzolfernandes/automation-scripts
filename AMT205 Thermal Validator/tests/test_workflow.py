from __future__ import annotations

import unittest
from copy import deepcopy

from amt205_validator.configuration import load_config
from amt205_validator.protocol import StatusReading
from amt205_validator.workflow import WorkflowAdvisor, format_remaining


def reading(relative: float, *, temperature: float = 33.0, executing: bool = False,
            elapsed: float | None = None, progress: int | None = None,
            error_latched: bool = False, error_code: int = 0) -> StatusReading:
    temperatures = {
        name: temperature
        for name in (
            "V1 Board 1", "V2 Board 1", "V1 Board 2", "V2 Board 2",
            "I1 Board 1", "I2 Board 1", "I1 Board 2", "I2 Board 2",
            "Main Board 1", "Main Board 2",
        )
    }
    return StatusReading(
        1, 0.0, relative, "", temperatures, "V1 Board 1", temperature,
        "Main Board 1", temperature, executing, error_latched, error_code,
        progress, elapsed,
    )


class WorkflowAdvisorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_config()
        self.config["initial_baseline"]["minimum_observation_s"] = 1.0
        self.config["thermal_recovery"]["hold_s"] = 1.0
        self.config["thermal_recovery"]["maximum_duration_s"] = 10.0
        self.config["thermal_recovery"]["maximum_sample_gap_s"] = 10.0
        self.advisor = WorkflowAdvisor(self.config, {"profile": "current_50", "fan_mode": "NORMAL"})

    @staticmethod
    def confirm_initial(advisor: WorkflowAdvisor, *, start: float = 0.0, temperature: float = 33.0) -> None:
        advisor.update(reading(start, temperature=temperature), "NORMAL")
        advisor.update(reading(start + 1.0, temperature=temperature), "NORMAL")

    def test_countdown_rounds_up_and_never_claims_a_partial_second(self) -> None:
        self.assertEqual(format_remaining(0), "00:00")
        self.assertEqual(format_remaining(0.0001), "00:01")
        self.assertEqual(format_remaining(60.1), "01:01")

    def test_automatic_baseline_uses_the_fan_specific_two_minute_window(self) -> None:
        config = load_config()
        config["initial_baseline"]["maximum_sample_gap_s"] = 200.0
        advisor = WorkflowAdvisor(config, {"profile": "current_50", "fan_mode": "NORMAL"})
        self.assertEqual(advisor.update(reading(0, temperature=32.5), "NORMAL").code, "OBSERVING_INITIAL_BASELINE")
        self.assertEqual(advisor.update(reading(119.999, temperature=32.7), "NORMAL").code, "OBSERVING_INITIAL_BASELINE")
        step = advisor.update(reading(120.0, temperature=32.7), "NORMAL")
        self.assertEqual(step.code, "READY_TO_INJECT")
        self.assertAlmostEqual(advisor.initial_baseline_c, 32.7)
        self.assertIn("not an injection-stability acceptance rule", step.detail)
        self.assertTrue(advisor.initial_confirmed)

    def test_baseline_rejects_temperature_outside_the_operational_envelope(self) -> None:
        step = self.advisor.update(reading(0, temperature=35.0), "NORMAL")
        self.assertEqual(step.code, "WAITING_BASELINE_ENVELOPE")
        self.assertFalse(self.advisor.initial_confirmed)

    def test_baseline_waits_while_the_temperature_trend_is_too_high(self) -> None:
        config = deepcopy(self.config)
        config["initial_baseline"]["minimum_observation_s"] = 180.0
        config["initial_baseline"]["maximum_range_c"] = 5.0
        config["initial_baseline"]["maximum_sample_gap_s"] = 100.0
        advisor = WorkflowAdvisor(config, {"profile": "current_50", "fan_mode": "NORMAL"})
        advisor.update(reading(0, temperature=32.0), "NORMAL")
        advisor.update(reading(90, temperature=32.5), "NORMAL")
        step = advisor.update(reading(180, temperature=33.0), "NORMAL")
        self.assertEqual(step.code, "WAITING_BASELINE_TREND")
        self.assertFalse(advisor.initial_confirmed)

    def test_packet_gap_restarts_initial_baseline(self) -> None:
        self.advisor.update(reading(0), "NORMAL")
        step = self.advisor.update(reading(6), "NORMAL")
        self.assertEqual(step.code, "OBSERVING_INITIAL_BASELINE")
        self.assertAlmostEqual(step.remaining_s, 1.0)

    def test_previously_ready_baseline_is_revoked_after_a_gap(self) -> None:
        self.confirm_initial(self.advisor)
        self.assertTrue(self.advisor.initial_confirmed)
        step = self.advisor.update(reading(7), "NORMAL")
        self.assertEqual(step.code, "OBSERVING_INITIAL_BASELINE")
        self.assertFalse(self.advisor.initial_confirmed)
        step = self.advisor.update(reading(8, executing=True, elapsed=0.0), "NORMAL")
        self.assertEqual(step.code, "INVALID_START")

    def test_explicitly_approved_window_restores_countdown(self) -> None:
        config = deepcopy(self.config)
        config["initial_baseline"]["enabled"] = False
        config["stability"]["window_approved"] = True
        config["stability"]["window_s"] = 60.0
        advisor = WorkflowAdvisor(config, {"profile": "current_50", "fan_mode": "NORMAL"})
        self.assertEqual(advisor.update(reading(0), "NORMAL").code, "STABILIZING")
        step = advisor.update(reading(59.999), "NORMAL")
        self.assertEqual(step.code, "STABILIZING")
        self.assertEqual(format_remaining(step.remaining_s), "00:01")
        self.assertEqual(advisor.update(reading(60.0), "NORMAL").code, "READY_TO_INJECT")

    def test_wrong_fan_mode_blocks_the_stability_timer(self) -> None:
        step = self.advisor.update(reading(0), "MAX")
        self.assertEqual(step.code, "FAN_MISMATCH")
        self.assertEqual(step.level, "WARNING")

    def test_fan_mode_change_during_injection_invalidates_the_run(self) -> None:
        advisor = WorkflowAdvisor(self.config, {"profile": "current_50", "fan_mode": "MAX"})
        advisor.update(reading(0), "MAX")
        advisor.update(reading(1, temperature=33.0), "MAX")
        advisor.update(reading(2, executing=True, elapsed=0.0), "MAX")
        step = advisor.update(reading(3, executing=True, elapsed=1.0), "NORMAL")
        self.assertEqual(step.code, "FAN_MISMATCH_DURING_INJECTION")
        self.assertTrue(advisor.fan_mode_mismatch_observed)

    def test_historical_profile_guides_reference_duration_and_post_test(self) -> None:
        self.confirm_initial(self.advisor)
        self.assertEqual(self.advisor.update(reading(2, executing=True, elapsed=0.0), "NORMAL").code, "INJECTING_TO_REFERENCE")
        self.assertEqual(self.advisor.update(reading(181.399, executing=True, elapsed=179.399), "NORMAL").code, "INJECTING_TO_REFERENCE")
        self.assertEqual(self.advisor.update(reading(181.4, executing=True, elapsed=179.4), "NORMAL").code, "REFERENCE_REACHED")
        self.assertEqual(self.advisor.update(reading(187, executing=True, elapsed=185.0), "NORMAL").code, "DURATION_COMPLETE")
        self.assertEqual(self.advisor.update(reading(188, executing=False), "NORMAL").code, "THERMAL_RECOVERY")
        self.assertEqual(self.advisor.update(reading(188.999, executing=False), "NORMAL").code, "THERMAL_RECOVERY")
        self.assertEqual(self.advisor.update(reading(189, executing=False), "NORMAL").code, "COMPLETE")
        self.assertTrue(self.advisor.recovery_reached)
        self.assertEqual(self.advisor.recovery_time_to_target_s, 0.0)

    def test_injection_before_initial_confirmation_requires_repetition(self) -> None:
        step = self.advisor.update(reading(1, temperature=35.0, executing=True, elapsed=0.0), "NORMAL")
        self.assertEqual(step.code, "INVALID_START")
        step = self.advisor.update(reading(2, executing=True, elapsed=1.0), "NORMAL")
        self.assertEqual(step.code, "INVALID_START")
        self.advisor.update(reading(3, executing=False), "NORMAL")
        step = self.advisor.update(reading(4, executing=False), "NORMAL")
        self.assertEqual(step.code, "REPEAT_REQUIRED")

    def test_ia1_after_reference_completes_with_electrical_review(self) -> None:
        self.confirm_initial(self.advisor)
        self.advisor.update(reading(2, executing=True, elapsed=0.0), "NORMAL")
        self.advisor.update(reading(181.9, executing=True, elapsed=180.0), "NORMAL")
        step = self.advisor.update(reading(182, executing=False, elapsed=180.0, progress=14), "NORMAL")
        self.assertEqual(step.code, "THERMAL_RECOVERY")
        self.assertEqual(step.level, "WARNING")
        step = self.advisor.update(reading(182.25, executing=False, error_latched=True), "NORMAL")
        self.assertEqual(step.code, "THERMAL_RECOVERY")
        step = self.advisor.update(reading(183, executing=False), "NORMAL")
        self.assertEqual(step.code, "COMPLETE")
        self.assertFalse(self.advisor.thermal_observed)
        self.assertTrue(self.advisor.error_termination_observed)
        self.assertTrue(self.advisor.historical_stress_endpoint_observed)

    def test_ia1_before_reference_requires_repetition(self) -> None:
        self.confirm_initial(self.advisor)
        self.advisor.update(reading(2, executing=True, elapsed=0.0), "NORMAL")
        self.advisor.update(reading(121, executing=False, elapsed=120.0, progress=14), "NORMAL")
        step = self.advisor.update(reading(122, executing=False), "NORMAL")
        self.assertEqual(step.code, "REPEAT_REQUIRED")
        self.assertTrue(self.advisor.historical_stress_endpoint_observed)

    def test_code_6_is_required_for_thermal_error(self) -> None:
        self.confirm_initial(self.advisor)
        step = self.advisor.update(reading(2, executing=True, elapsed=0.0, progress=14, error_latched=True, error_code=6), "NORMAL")
        self.assertEqual(step.code, "THERMAL_STOP")
        self.assertTrue(self.advisor.thermal_observed)

    def test_temperature_profile_uses_the_approved_stability_rule(self) -> None:
        advisor = WorkflowAdvisor(self.config, {"profile": "current_25", "fan_mode": "NORMAL"})
        self.confirm_initial(advisor)
        step = advisor.update(reading(2, temperature=40.0, executing=True, elapsed=0.0), "NORMAL")
        self.assertEqual(step.code, "INJECTING_TO_STABILITY")
        self.assertIn("trend", step.detail)

    def test_max_power_recovery_uses_25_degree_target(self) -> None:
        advisor = WorkflowAdvisor(self.config, {"profile": "current_50", "fan_mode": "MAX"})
        advisor.update(reading(0, temperature=25.0), "MAX")
        advisor.update(reading(1, temperature=25.0), "MAX")
        advisor.update(reading(2, temperature=50.0, executing=True, elapsed=180.0), "MAX")
        step = advisor.update(reading(3, temperature=26.1, executing=False), "MAX")
        self.assertEqual(step.code, "THERMAL_RECOVERY")
        self.assertIsNone(advisor.recovery_time_to_target_s)
        advisor.update(reading(4, temperature=26.0, executing=False), "MAX")
        step = advisor.update(reading(5, temperature=25.8, executing=False), "MAX")
        self.assertEqual(step.code, "COMPLETE")
        self.assertEqual(advisor.recovery_target_c, 25.0)
        self.assertEqual(advisor.recovery_time_to_target_s, 1.0)

    def test_recovery_timeout_is_recorded_without_changing_test_acceptance(self) -> None:
        config = deepcopy(self.config)
        config["thermal_recovery"]["maximum_duration_s"] = 2.0
        advisor = WorkflowAdvisor(config, {"profile": "current_50", "fan_mode": "NORMAL"})
        self.confirm_initial(advisor)
        advisor.update(reading(2, executing=True, elapsed=180.0), "NORMAL")
        advisor.update(reading(3, temperature=45.0, executing=False), "NORMAL")
        step = advisor.update(reading(5, temperature=40.0, executing=False), "NORMAL")
        self.assertEqual(step.code, "COMPLETE")
        self.assertTrue(advisor.recovery_timed_out)
        self.assertFalse(advisor.recovery_reached)


if __name__ == "__main__":
    unittest.main()

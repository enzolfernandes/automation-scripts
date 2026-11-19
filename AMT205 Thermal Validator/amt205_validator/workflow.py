from __future__ import annotations

import math
import statistics
from collections import deque
from dataclasses import asdict, dataclass

from .criteria import (
    effective_historical_reference_s,
    effective_recommended_duration_s,
    historical_stress_endpoint_enabled,
    initial_target,
)
from .protocol import StatusReading


@dataclass(frozen=True, slots=True)
class WorkflowStep:
    code: str
    title: str
    instruction: str
    detail: str = ""
    remaining_s: float | None = None
    total_s: float | None = None
    level: str = "INFO"

    def to_dict(self) -> dict:
        return asdict(self)


def format_remaining(seconds: float | None) -> str:
    """Format a conservative countdown: any fraction is rounded up."""
    if seconds is None:
        return "—"
    whole_seconds = math.ceil(max(0.0, seconds))
    minutes, remainder = divmod(whole_seconds, 60)
    return f"{minutes:02d}:{remainder:02d}"


class WorkflowAdvisor:
    """Passive, packet-driven operator guidance for a recorded test."""

    TERMINAL_CODES = {"COMPLETE", "REPEAT_REQUIRED"}

    def __init__(self, config: dict, metadata: dict) -> None:
        self.config = config
        self.metadata = metadata
        self.profile_key = metadata.get("profile", "current_50")
        self.profile = config["profiles"][self.profile_key]
        self.expected_fan_mode = metadata.get("fan_mode", "MAX").upper()
        self.target_c = initial_target(self.expected_fan_mode, config)
        self.tolerance_c = float(config["initial_temperature_c"]["tolerance"])
        baseline_config = config.get("initial_baseline", {})
        self.baseline_enabled = bool(baseline_config.get("enabled", False))
        self.baseline_use_fan_target = bool(baseline_config.get("use_fan_mode_target", False))
        self.baseline_minimum_c = float(baseline_config.get("minimum_c", 24.0))
        self.baseline_maximum_c = float(baseline_config.get("maximum_c", 34.0))
        self.baseline_window_s = float(baseline_config.get("minimum_observation_s", 180.0))
        self.baseline_range_limit_c = float(baseline_config.get("maximum_range_c", 0.5))
        self.baseline_slope_limit_c_per_min = float(baseline_config.get("maximum_abs_slope_c_per_min", 0.1))
        self.baseline_maximum_sample_gap_s = float(baseline_config.get("maximum_sample_gap_s", 5.0))
        configured_window = config["stability"].get("window_s")
        self.window_approved = bool(config["stability"].get("window_approved", False) and configured_window is not None)
        self.window_s = float(configured_window) if self.window_approved else None
        self.range_limit_c = float(config["stability"]["maximum_range_c"])
        self.slope_limit_c_per_min = float(config["stability"].get("maximum_abs_slope_c_per_min", 0.2))
        self.post_test_s = float(config.get("workflow", {}).get("post_test_evidence_s", 10.0))
        recovery_config = config.get("thermal_recovery", {})
        self.recovery_enabled = bool(recovery_config.get("enabled", False))
        self.recovery_target_c = self.target_c
        self.recovery_tolerance_c = self.tolerance_c
        self.recovery_hold_s = float(recovery_config.get("hold_s", 120.0))
        self.recovery_maximum_duration_s = float(recovery_config.get("maximum_duration_s", 1800.0))
        self.recovery_maximum_sample_gap_s = float(recovery_config.get("maximum_sample_gap_s", 5.0))
        self._precondition_points: deque[tuple[float, float]] = deque()
        self._injection_points: deque[tuple[float, float]] = deque()
        self._recovery_points: deque[tuple[float, float]] = deque()
        self.initial_baseline_c: float | None = None
        self.initial_baseline_sensor: str | None = None
        self.initial_baseline_range_c: float | None = None
        self.initial_baseline_slope_c_per_min: float | None = None
        self.initial_baseline_observation_s: float = 0.0
        self.initial_baseline_confirmed_at_s: float | None = None
        self.initial_confirmed = False
        self.injection_seen = False
        self.injection_stable = False
        self.invalid_start = False
        self.interrupted = False
        self.thermal_observed = False
        self.error_termination_observed = False
        self.historical_stress_endpoint_observed = False
        self.fan_mode_mismatch_observed = False
        self.error_termination_relative: float | None = None
        self.injection_started_at: float | None = None
        self.injection_stopped_at: float | None = None
        self.last_injection_elapsed_s: float | None = None
        self.recovery_start_temperature_c: float | None = None
        self.recovery_end_temperature_c: float | None = None
        self.recovery_sensor: str | None = None
        self.recovery_target_entered_at_s: float | None = None
        self.recovery_time_to_target_s: float | None = None
        self.recovery_confirmed_at_s: float | None = None
        self.recovery_reached = False
        self.recovery_timed_out = False
        self.current_step = WorkflowStep(
            "WAITING_READING",
            "Waiting for recorded data",
            "Keep injection OFF while the first recorded temperature packet is received.",
        )

    @staticmethod
    def _range(points: deque[tuple[float, float]]) -> float:
        values = [value for _, value in points]
        return max(values) - min(values) if values else 0.0

    @staticmethod
    def _coverage(points: deque[tuple[float, float]]) -> float:
        return points[-1][0] - points[0][0] if len(points) >= 2 else 0.0

    @staticmethod
    def _slope_c_per_min(points: deque[tuple[float, float]]) -> float:
        if len(points) < 2:
            return 0.0
        mean_time = sum(point[0] for point in points) / len(points)
        mean_temperature = sum(point[1] for point in points) / len(points)
        denominator = sum((point[0] - mean_time) ** 2 for point in points)
        if denominator == 0:
            return 0.0
        slope_c_per_s = sum(
            (time_s - mean_time) * (temperature_c - mean_temperature)
            for time_s, temperature_c in points
        ) / denominator
        return slope_c_per_s * 60.0

    def _reset_initial_baseline(self) -> None:
        self._precondition_points.clear()
        self.initial_confirmed = False
        self.initial_baseline_c = None
        self.initial_baseline_sensor = None
        self.initial_baseline_range_c = None
        self.initial_baseline_slope_c_per_min = None
        self.initial_baseline_observation_s = 0.0
        self.initial_baseline_confirmed_at_s = None

    def _step(self, code: str, title: str, instruction: str, detail: str = "", remaining_s: float | None = None,
              total_s: float | None = None, level: str = "INFO") -> WorkflowStep:
        self.current_step = WorkflowStep(code, title, instruction, detail, remaining_s, total_s, level)
        return self.current_step

    def _precondition(self, reading: StatusReading, observed_fan_mode: str) -> WorkflowStep:
        if observed_fan_mode in ("NORMAL", "MAX") and observed_fan_mode != self.expected_fan_mode:
            self._reset_initial_baseline()
            expected = "Max Power" if self.expected_fan_mode == "MAX" else "Normal"
            observed = "Max Power" if observed_fan_mode == "MAX" else "Normal"
            return self._step(
                "FAN_MISMATCH",
                "Fan Mode does not match the session",
                f"Keep injection OFF and set Fan Mode to {expected} in AMPro.",
                f"Observed: {observed}. Initial-condition confirmation is blocked.",
                level="WARNING",
            )

        if self.baseline_enabled:
            return self._automatic_baseline(reading)

        low = self.target_c - self.tolerance_c
        high = self.target_c + self.tolerance_c
        current = reading.hottest_control_c
        if not low <= current <= high:
            self._precondition_points.clear()
            return self._step(
                "WAITING_TEMPERATURE",
                "Waiting for initial temperature",
                f"Keep injection OFF. Bring the hottest V/I sensor to {low:.1f}–{high:.1f} °C.",
                f"Current control temperature: {current:.1f} °C.",
            )

        if not self.window_approved:
            self.initial_confirmed = True
            return self._step(
                "READY_TO_INJECT",
                "Initial temperature confirmed",
                "Start injection in AMPro.",
                f"Recorded control temperature: {current:.1f} °C within {low:.1f}–{high:.1f} °C. No minimum stabilization duration is approved.",
            )

        self._precondition_points.append((reading.relative, current))
        current_range = self._range(self._precondition_points)
        if current_range > self.range_limit_c:
            self._precondition_points.clear()
            self._precondition_points.append((reading.relative, current))
            return self._step(
                "STABILITY_RESTARTED",
                "Initial stability restarted",
                "Keep injection OFF while a new minimum stability window is recorded.",
                f"The observed range exceeded {self.range_limit_c:.1f} °C. Current control temperature: {current:.1f} °C.",
                self.window_s,
                self.window_s,
                "WARNING",
            )

        coverage = self._coverage(self._precondition_points)
        remaining = max(0.0, self.window_s - coverage)
        if remaining > 0:
            return self._step(
                "STABILIZING",
                "Stabilizing initial temperature",
                f"Keep injection OFF for at least {format_remaining(remaining)} more.",
                f"Control: {current:.1f} °C · observed range: {current_range:.1f} °C · required range: ≤ {self.range_limit_c:.1f} °C.",
                remaining,
                self.window_s,
            )

        self.initial_confirmed = True
        return self._step(
            "READY_TO_INJECT",
            "Initial condition confirmed",
            "Start injection in AMPro.",
            f"At least {self.window_s:.0f} s were recorded within {low:.1f}–{high:.1f} °C and the permitted stability range.",
        )

    def _automatic_baseline(self, reading: StatusReading) -> WorkflowStep:
        current = reading.hottest_control_c
        if self.baseline_use_fan_target:
            low = self.target_c - self.tolerance_c
            high = self.target_c + self.tolerance_c
        else:
            low = self.baseline_minimum_c
            high = self.baseline_maximum_c
        if not low <= current <= high:
            self._reset_initial_baseline()
            return self._step(
                "WAITING_BASELINE_ENVELOPE",
                "Waiting for an eligible initial temperature",
                f"Keep injection OFF. The hottest V/I sensor must remain within {low:.1f}–{high:.1f} °C.",
                f"Current control temperature: {current:.1f} °C. The operational baseline has not started.",
                level="WARNING",
            )

        if (
            self._precondition_points
            and reading.relative - self._precondition_points[-1][0] > self.baseline_maximum_sample_gap_s
        ):
            self._reset_initial_baseline()

        self._precondition_points.append((reading.relative, current))
        cutoff = reading.relative - self.baseline_window_s
        while len(self._precondition_points) >= 2 and self._precondition_points[1][0] <= cutoff:
            self._precondition_points.popleft()

        observed_range = self._range(self._precondition_points)
        coverage = self._coverage(self._precondition_points)
        slope = self._slope_c_per_min(self._precondition_points)
        self.initial_baseline_range_c = observed_range
        self.initial_baseline_slope_c_per_min = slope
        self.initial_baseline_observation_s = coverage

        if observed_range > self.baseline_range_limit_c:
            self._reset_initial_baseline()
            self._precondition_points.append((reading.relative, current))
            self.initial_baseline_range_c = 0.0
            return self._step(
                "BASELINE_RESTARTED",
                "Initial baseline restarted",
                f"Keep injection OFF for at least {format_remaining(self.baseline_window_s)} more.",
                f"The observed range exceeded {self.baseline_range_limit_c:.1f} °C; a new operational baseline started at {current:.1f} °C.",
                self.baseline_window_s,
                self.baseline_window_s,
                "WARNING",
            )

        remaining = max(0.0, self.baseline_window_s - coverage)
        if remaining > 0:
            return self._step(
                "OBSERVING_INITIAL_BASELINE",
                "Observing initial temperature baseline",
                f"Keep injection OFF for at least {format_remaining(remaining)} more.",
                f"Control: {current:.1f} °C · range: {observed_range:.1f}/{self.baseline_range_limit_c:.1f} °C · trend: {slope:+.3f} °C/min.",
                remaining,
                self.baseline_window_s,
            )

        if abs(slope) > self.baseline_slope_limit_c_per_min:
            self.initial_confirmed = False
            self.initial_baseline_c = None
            self.initial_baseline_sensor = None
            self.initial_baseline_confirmed_at_s = None
            return self._step(
                "WAITING_BASELINE_TREND",
                "Initial temperature is still drifting",
                "Keep injection OFF until the temperature trend becomes sufficiently low.",
                f"Observed trend: {slope:+.3f} °C/min · permitted: ±{self.baseline_slope_limit_c_per_min:.3f} °C/min · range: {observed_range:.1f} °C.",
                level="WARNING",
            )

        self.initial_confirmed = True
        self.initial_baseline_c = float(statistics.median(value for _, value in self._precondition_points))
        self.initial_baseline_sensor = reading.hottest_control_name
        if self.initial_baseline_confirmed_at_s is None:
            self.initial_baseline_confirmed_at_s = reading.relative
        return self._step(
            "READY_TO_INJECT",
            "Initial operational baseline confirmed",
            "Start injection in AMPro.",
            f"Baseline: {self.initial_baseline_c:.1f} °C ({self.initial_baseline_sensor}) · {coverage:.0f} s · range {observed_range:.1f} °C · trend {slope:+.3f} °C/min. This prepares the test and is not an injection-stability acceptance rule.",
        )

    def _observed_injection_elapsed(self, reading: StatusReading) -> float:
        if reading.profile_elapsed_s is not None:
            elapsed = float(reading.profile_elapsed_s)
        elif self.injection_started_at is not None:
            elapsed = max(0.0, reading.relative - self.injection_started_at)
        else:
            elapsed = 0.0
        self.last_injection_elapsed_s = max(self.last_injection_elapsed_s or 0.0, elapsed)
        return elapsed

    def _historical_injection(self, reading: StatusReading, elapsed: float) -> WorkflowStep:
        reference = effective_historical_reference_s(self.profile, self.expected_fan_mode, self.config)
        recommended = effective_recommended_duration_s(self.profile, self.expected_fan_mode, self.config)
        if self.thermal_observed:
            verdict = "before" if elapsed < reference else "after"
            return self._step(
                "THERMAL_STOP",
                "Thermal Error detected",
                "Stop injection in AMPro and keep recording through thermal recovery.",
                f"Thermal Error occurred {verdict} the {reference:.1f} s historical reference (observed: {elapsed:.1f} s).",
                level="FAIL" if elapsed < reference else "WARNING",
            )
        if elapsed < reference:
            remaining = reference - elapsed
            return self._step(
                "INJECTING_TO_REFERENCE",
                "Injection in progress",
                f"Continue injection for at least {format_remaining(remaining)} more to reach the historical reference.",
                f"Observed State Time: {elapsed:.1f} s · minimum reference: {reference:.1f} s.",
                remaining,
                reference,
            )
        if elapsed < recommended:
            remaining = recommended - elapsed
            return self._step(
                "REFERENCE_REACHED",
                "Historical reference reached",
                f"Continue injection for at least {format_remaining(remaining)} more or until Thermal Error.",
                f"The {reference:.1f} s acceptance reference has been met; recommended duration: {recommended:.1f} s.",
                remaining,
                recommended,
            )
        return self._step(
            "DURATION_COMPLETE",
            "Minimum test duration completed",
            "Stop injection in AMPro and keep recording through thermal recovery.",
            f"Observed State Time: {elapsed:.1f} s · recommended minimum: {recommended:.1f} s.",
        )

    def _stability_injection(self, reading: StatusReading, elapsed: float) -> WorkflowStep:
        self._injection_points.append((reading.relative, reading.hottest_control_c))
        maximum_duration = float(self.profile.get("maximum_duration_s", 1800.0))
        if self.thermal_observed:
            return self._step(
                "THERMAL_STOP",
                "Thermal Error detected",
                "Stop injection in AMPro and keep recording through thermal recovery.",
                f"Thermal Error was observed at approximately {elapsed:.1f} s.",
                level="FAIL",
            )
        if not self.window_approved:
            if elapsed >= maximum_duration:
                return self._step(
                    "DURATION_COMPLETE",
                    "Configured safety duration reached",
                    "Stop injection in AMPro and keep recording through thermal recovery.",
                    f"Observed duration: {elapsed:.1f} s · configured safety maximum: {maximum_duration:.1f} s. This is not a stabilization criterion.",
                    level="WARNING",
                )
            return self._step(
                "INJECTING_OBSERVATION",
                "Injection in progress",
                "Continue injection while observing temperature and AMPro events.",
                f"Observed time: {elapsed:.1f} s · control temperature: {reading.hottest_control_c:.1f} °C. No approved stabilization window is active; this run remains informational until the acceptance criteria are configured.",
                level="WARNING",
            )

        cutoff = reading.relative - self.window_s
        while len(self._injection_points) >= 2 and self._injection_points[1][0] <= cutoff:
            self._injection_points.popleft()
        phase_range = self._range(self._injection_points)
        phase_coverage = self._coverage(self._injection_points)
        phase_slope = self._slope_c_per_min(self._injection_points)
        self.injection_stable = (
            phase_coverage >= self.window_s
            and phase_range <= self.range_limit_c
            and abs(phase_slope) <= self.slope_limit_c_per_min
        )
        if self.injection_stable:
            return self._step(
                "INJECTION_STABLE",
                "Injection temperature stabilized",
                "Stop injection in AMPro and keep recording through thermal recovery.",
                f"At least {self.window_s:.0f} s were recorded with a range of {phase_range:.1f} °C and trend {phase_slope:+.3f} °C/min.",
            )
        if elapsed >= maximum_duration:
            return self._step(
                "DURATION_COMPLETE",
                "Configured maximum duration reached",
                "Stop injection in AMPro and keep recording through thermal recovery.",
                f"Observed duration: {elapsed:.1f} s · configured maximum: {maximum_duration:.1f} s.",
                level="WARNING",
            )
        remaining = max(0.0, self.window_s - phase_coverage)
        remaining_display = remaining if phase_range <= self.range_limit_c and abs(phase_slope) <= self.slope_limit_c_per_min else None
        return self._step(
            "INJECTING_TO_STABILITY",
            "Injection in progress",
            "Continue injection until temperature stability or Thermal Error is detected.",
            f"Current stability coverage: {phase_coverage:.1f}/{self.window_s:.1f} s · range: {phase_range:.1f}/{self.range_limit_c:.1f} °C · trend: {phase_slope:+.3f}/±{self.slope_limit_c_per_min:.3f} °C/min.",
            remaining_display,
            self.window_s,
        )

    def _active_injection(self, reading: StatusReading) -> WorkflowStep:
        elapsed = self._observed_injection_elapsed(reading)
        if self.historical_stress_endpoint_observed:
            return self._step(
                "HISTORICAL_STRESS_ENDPOINT",
                "IA1 Other electrical endpoint detected",
                "Keep recording; thermal and electrical results will be evaluated separately.",
                f"Progress status 14 ended electrical output at approximately {elapsed:.1f} s; it does not prove thermal stabilization.",
                level="WARNING",
            )
        if self.error_termination_observed and not self.thermal_observed:
            return self._step(
                "ERROR_TERMINATION",
                "AMPro error termination detected",
                "Keep recording and verify the error text shown in AMPro.",
                f"Progress status 14 ended the injection at approximately {elapsed:.1f} s, but it does not identify the error type.",
                level="WARNING",
            )
        criterion = self.profile.get("criterion", "monitor_only")
        if criterion == "historical_time":
            return self._historical_injection(reading, elapsed)
        if criterion in ("stable_temperature", "reach_stability"):
            return self._stability_injection(reading, elapsed)
        maximum_duration = float(self.profile.get("maximum_duration_s", 1800.0))
        if elapsed >= maximum_duration:
            return self._step(
                "DURATION_COMPLETE",
                "Configured duration reached",
                "Stop injection in AMPro and keep recording through thermal recovery.",
                f"Free Profile duration: {elapsed:.1f}/{maximum_duration:.1f} s.",
            )
        return self._step(
            "MONITORING_INJECTION",
            "Free Profile injection in progress",
            "Continue or stop the injection according to the operator-defined procedure.",
            f"Observed duration: {elapsed:.1f} s · configured maximum: {maximum_duration:.1f} s.",
        )

    def _post_test(self, reading: StatusReading) -> WorkflowStep:
        if self.injection_stopped_at is None:
            self.injection_stopped_at = reading.relative
            self.recovery_start_temperature_c = reading.hottest_control_c
            self.recovery_sensor = reading.hottest_control_name
        post_elapsed = max(0.0, reading.relative - self.injection_stopped_at)
        criterion = self.profile.get("criterion", "monitor_only")
        unidentified_error = self.error_termination_observed and not self.thermal_observed
        historical_endpoint = self.historical_stress_endpoint_observed
        unidentified_error = unidentified_error and not historical_endpoint
        early = False
        if criterion == "historical_time":
            reference = effective_historical_reference_s(self.profile, self.expected_fan_mode, self.config)
            early = (
                (self.last_injection_elapsed_s or 0.0) < reference
                and not self.thermal_observed
            )
        elif criterion in ("stable_temperature", "reach_stability"):
            early = historical_endpoint and not self.injection_stable

        if self.recovery_enabled:
            self.recovery_end_temperature_c = reading.hottest_control_c
            threshold_c = self.recovery_target_c + self.recovery_tolerance_c
            if (
                self._recovery_points
                and reading.relative - self._recovery_points[-1][0] > self.recovery_maximum_sample_gap_s
            ):
                self._recovery_points.clear()
                self.recovery_target_entered_at_s = None
            if reading.hottest_control_c <= threshold_c:
                if not self._recovery_points:
                    self.recovery_target_entered_at_s = post_elapsed
                self._recovery_points.append((reading.relative, reading.hottest_control_c))
            else:
                self._recovery_points.clear()
                self.recovery_target_entered_at_s = None

            recovery_coverage = self._coverage(self._recovery_points)
            if recovery_coverage >= self.recovery_hold_s:
                self.recovery_reached = True
                self.recovery_time_to_target_s = self.recovery_target_entered_at_s
                self.recovery_confirmed_at_s = post_elapsed
            self.recovery_timed_out = not self.recovery_reached and post_elapsed >= self.recovery_maximum_duration_s
            if not self.recovery_reached and not self.recovery_timed_out:
                hold_remaining = max(0.0, self.recovery_hold_s - recovery_coverage)
                if self._recovery_points:
                    instruction = f"Keep recording while the temperature remains at or below {threshold_c:.1f} °C for {format_remaining(hold_remaining)} more."
                    detail = (
                        f"Fan Mode target: {self.recovery_target_c:.1f} °C ± {self.recovery_tolerance_c:.1f} °C · "
                        f"current: {reading.hottest_control_c:.1f} °C · hold: {recovery_coverage:.1f}/{self.recovery_hold_s:.1f} s."
                    )
                    remaining = hold_remaining
                    total = self.recovery_hold_s
                else:
                    instruction = f"Keep recording until the hottest V/I sensor reaches {threshold_c:.1f} °C or below."
                    detail = (
                        f"Fan Mode target: {self.recovery_target_c:.1f} °C ± {self.recovery_tolerance_c:.1f} °C · "
                        f"current: {reading.hottest_control_c:.1f} °C · elapsed recovery: {post_elapsed:.1f}/{self.recovery_maximum_duration_s:.1f} s."
                    )
                    remaining = max(0.0, self.recovery_maximum_duration_s - post_elapsed)
                    total = self.recovery_maximum_duration_s
                return self._step(
                    "THERMAL_RECOVERY",
                    "Thermal recovery in progress",
                    instruction,
                    detail,
                    remaining,
                    total,
                    "WARNING" if early or unidentified_error or historical_endpoint else "INFO",
                )
        else:
            remaining = max(0.0, self.post_test_s - post_elapsed)
            if remaining > 0:
                if historical_endpoint:
                    detail = "IA1 Other was recorded; it is an electrical occurrence and does not complete an unmet thermal criterion."
                elif unidentified_error:
                    detail = "Injection ended with an AMPro error whose type is not decoded; this run will require repetition."
                else:
                    detail = "The injection ended before the minimum reference; this run will require repetition." if early else "Injection is OFF. Final temperatures and state are still being recorded."
                return self._step(
                    "POST_TEST_EVIDENCE",
                    "Capturing post-test evidence",
                    f"Keep recording for at least {format_remaining(remaining)} more.",
                    detail,
                    remaining,
                    self.post_test_s,
                    "WARNING" if early or unidentified_error or historical_endpoint else "INFO",
                )

        if self.invalid_start or self.interrupted or early or unidentified_error:
            reason = "The injection ended with an AMPro error whose type was not decoded." if unidentified_error else "The minimum workflow was not completed continuously."
            if self.recovery_timed_out:
                reason += f" Thermal recovery did not reach {self.recovery_target_c:.1f} °C ± {self.recovery_tolerance_c:.1f} °C within {self.recovery_maximum_duration_s:.0f} s."
            return self._step(
                "REPEAT_REQUIRED",
                "Recording complete — repeat required",
                "The report will be generated automatically. Repeat the test from the initial condition.",
                reason,
                level="WARNING",
            )

        if self.recovery_timed_out:
            if historical_endpoint:
                electrical_detail = " IA1 Other was recorded separately."
            else:
                electrical_detail = ""
            return self._step(
                "COMPLETE",
                "Test complete — recovery target not reached",
                "The report will be generated automatically.",
                f"The {self.recovery_target_c:.1f} °C target was not reached within {self.recovery_maximum_duration_s:.0f} s.{electrical_detail}",
                level="WARNING",
            )

        recovery_detail = (
            f"Thermal recovery reached {self.recovery_target_c:.1f} °C ± {self.recovery_tolerance_c:.1f} °C "
            f"after {self.recovery_time_to_target_s:.1f} s and remained within the criterion for {self.recovery_hold_s:.0f} s."
            if self.recovery_enabled and self.recovery_reached and self.recovery_time_to_target_s is not None
            else "The configured minimum post-test evidence window has been recorded."
        )
        return self._step(
            "COMPLETE",
            "Test sequence completed",
            "The report will be generated automatically.",
            recovery_detail,
        )

    def update(self, reading: StatusReading, observed_fan_mode: str) -> WorkflowStep:
        self.thermal_observed = self.thermal_observed or (reading.error_latched and reading.error_code == 6)
        self.error_termination_observed = self.error_termination_observed or reading.progress_status == 14
        if reading.progress_status == 14:
            self.error_termination_relative = reading.relative
        # Fan Mode is part of the approved thermal-test condition, not merely
        # an initial setup preference.  A switch to Normal during injection
        # invalidates the run so a reference time cannot be claimed for the
        # wrong cooling condition.
        if (
            observed_fan_mode in ("NORMAL", "MAX")
            and observed_fan_mode != self.expected_fan_mode
            and (reading.executing or (self.injection_seen and self.injection_stopped_at is None))
        ):
            self.fan_mode_mismatch_observed = True
            self.invalid_start = True
            return self._step(
                "FAN_MISMATCH_DURING_INJECTION",
                "Fan Mode changed during injection",
                f"Stop injection and repeat the test with Fan Mode {'Max Power' if self.expected_fan_mode == 'MAX' else 'Normal'} maintained throughout.",
                "The observed Fan Mode does not match the mode selected for this session.",
                level="WARNING",
            )
        criterion = self.profile.get("criterion", "monitor_only")
        self.historical_stress_endpoint_observed = self.historical_stress_endpoint_observed or bool(
            historical_stress_endpoint_enabled(self.config, criterion)
            and reading.progress_status == 14
            and not (reading.error_latched and reading.error_code == 6)
        )

        if not self.injection_seen and not reading.executing:
            return self._precondition(reading, observed_fan_mode)

        if reading.executing:
            if not self.injection_seen:
                self.injection_seen = True
                self.injection_started_at = reading.relative
                if not self.initial_confirmed:
                    self.invalid_start = True
                    return self._step(
                        "INVALID_START",
                        "Injection started before initial confirmation",
                        "Stop injection in AMPro and repeat after the initial condition is confirmed.",
                        "This recording may be retained as evidence, but its formal result must remain INCONCLUSIVE.",
                        level="WARNING",
                    )
            if self.invalid_start:
                return self._step(
                    "INVALID_START",
                    "Injection started before initial confirmation",
                    "Stop injection in AMPro and repeat after the initial condition is confirmed.",
                    "This recording may be retained as evidence, but its formal result must remain INCONCLUSIVE.",
                    level="WARNING",
                )
            if self.interrupted:
                return self._step(
                    "INJECTION_INTERRUPTED",
                    "Injection resumed after an interruption",
                    "Stop injection and repeat the test from the initial condition.",
                    "The configured workflow requires one continuous injection period.",
                    level="WARNING",
                )
            elif self.injection_stopped_at is not None:
                self.interrupted = True
                return self._step(
                    "INJECTION_INTERRUPTED",
                    "Injection resumed after an interruption",
                    "Stop injection and repeat the test from the initial condition.",
                    "The configured workflow requires one continuous injection period.",
                    level="WARNING",
                )
            return self._active_injection(reading)

        return self._post_test(reading)

    def summary(self) -> dict:
        return {
            "initial_confirmed": self.initial_confirmed,
            "injection_seen": self.injection_seen,
            "injection_stable": self.injection_stable,
            "invalid_start": self.invalid_start,
            "interrupted": self.interrupted,
            "thermal_observed": self.thermal_observed,
            "error_termination_observed": self.error_termination_observed,
            "historical_stress_endpoint_observed": self.historical_stress_endpoint_observed,
            "fan_mode_mismatch_observed": self.fan_mode_mismatch_observed,
            "stability_window_approved": self.window_approved,
            "stability_window_s": self.window_s,
            "stability_maximum_range_c": self.range_limit_c,
            "stability_maximum_abs_slope_c_per_min": self.slope_limit_c_per_min,
            "effective_historical_reference_s": (
                effective_historical_reference_s(self.profile, self.expected_fan_mode, self.config)
                if self.profile.get("criterion") == "historical_time" else None
            ),
            "initial_baseline": {
                "enabled": self.baseline_enabled,
                "confirmed": self.initial_confirmed,
                "temperature_c": self.initial_baseline_c,
                "sensor": self.initial_baseline_sensor,
                "range_c": self.initial_baseline_range_c,
                "slope_c_per_min": self.initial_baseline_slope_c_per_min,
                "observation_s": self.initial_baseline_observation_s,
                "confirmed_at_s": self.initial_baseline_confirmed_at_s,
            },
            "unidentified_error_termination": self.error_termination_observed and not self.thermal_observed and not self.historical_stress_endpoint_observed,
            "last_injection_elapsed_s": self.last_injection_elapsed_s,
            "thermal_recovery": {
                "enabled": self.recovery_enabled,
                "fan_mode": self.expected_fan_mode,
                "target_c": self.recovery_target_c,
                "tolerance_c": self.recovery_tolerance_c,
                "hold_s": self.recovery_hold_s,
                "maximum_duration_s": self.recovery_maximum_duration_s,
                "start_temperature_c": self.recovery_start_temperature_c,
                "end_temperature_c": self.recovery_end_temperature_c,
                "sensor": self.recovery_sensor,
                "time_to_target_s": self.recovery_time_to_target_s,
                "confirmed_at_s": self.recovery_confirmed_at_s,
                "reached": self.recovery_reached,
                "timed_out": self.recovery_timed_out,
            },
            "final_step": self.current_step.to_dict(),
        }

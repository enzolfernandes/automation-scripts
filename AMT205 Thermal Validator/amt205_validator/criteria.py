from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Iterable

from .protocol import StatusReading


@dataclass(slots=True)
class Evaluation:
    status: str
    reason: str
    elapsed_s: float | None
    max_control_c: float | None
    stable: bool
    initial_valid: bool = True
    thermal_status: str = "INCONCLUSIVE"
    electrical_status: str = "NOT ASSESSED"
    thermal_reason: str = ""
    electrical_reason: str = ""


@dataclass(slots=True)
class StabilityDiagnostic:
    window_s: float
    range_limit_c: float
    observed_duration_s: float
    minimum_range_c: float | None
    met: bool
    first_met_elapsed_s: float | None


class RollingStability:
    def __init__(self, window_s: float | None = None, range_limit_c: float = 1.0,
                 slope_limit_c_per_min: float | None = None) -> None:
        self.window_s = window_s
        self.range_limit_c = range_limit_c
        self.slope_limit_c_per_min = slope_limit_c_per_min
        self._points: deque[tuple[float, float]] = deque()

    def add(self, relative_s: float, value_c: float) -> None:
        if self.window_s is None:
            return
        self._points.append((relative_s, value_c))
        cutoff = relative_s - self.window_s
        while len(self._points) >= 2 and self._points[1][0] <= cutoff:
            self._points.popleft()

    def reset(self) -> None:
        self._points.clear()

    @property
    def range_c(self) -> float | None:
        if not self._points:
            return None
        values = [value for _, value in self._points]
        return max(values) - min(values)

    @property
    def coverage_s(self) -> float:
        if len(self._points) < 2:
            return 0.0
        return self._points[-1][0] - self._points[0][0]

    @property
    def slope_c_per_min(self) -> float | None:
        if len(self._points) < 2:
            return None
        mean_time = sum(point[0] for point in self._points) / len(self._points)
        mean_temperature = sum(point[1] for point in self._points) / len(self._points)
        denominator = sum((point[0] - mean_time) ** 2 for point in self._points)
        if denominator == 0:
            return 0.0
        slope_c_per_s = sum(
            (time_s - mean_time) * (temperature_c - mean_temperature)
            for time_s, temperature_c in self._points
        ) / denominator
        return slope_c_per_s * 60.0

    @property
    def stable(self) -> bool:
        value_range = self.range_c
        slope = self.slope_c_per_min
        return (
            self.window_s is not None
            and self.coverage_s >= self.window_s
            and value_range is not None
            and value_range <= self.range_limit_c
            and (
                self.slope_limit_c_per_min is None
                or (slope is not None and abs(slope) <= self.slope_limit_c_per_min)
            )
        )


def effective_historical_reference_s(profile_config: dict, fan_mode: str, config: dict) -> float:
    reference = float(profile_config["historical_time_s"])
    if fan_mode.upper() == "MAX":
        reference *= float(config.get("fan_mode", {}).get("max_power_historical_time_multiplier", 1.0))
    return reference


def effective_recommended_duration_s(profile_config: dict, fan_mode: str, config: dict) -> float:
    base_reference = float(profile_config["historical_time_s"])
    base_recommended = float(profile_config.get("recommended_duration_s", base_reference))
    effective_reference = effective_historical_reference_s(profile_config, fan_mode, config)
    return effective_reference + max(0.0, base_recommended - base_reference)


def analyze_stability(
    readings: Iterable[StatusReading],
    windows_s: Iterable[float],
    range_limit_c: float,
) -> list[StabilityDiagnostic]:
    """Analyze candidate windows without using them as acceptance criteria."""
    active = [item for item in readings if item.executing]
    if active:
        observed_duration = max(0.0, active[-1].relative - active[0].relative)
    else:
        observed_duration = 0.0
    diagnostics: list[StabilityDiagnostic] = []
    for raw_window in windows_s:
        window_s = float(raw_window)
        points: deque[tuple[float, float]] = deque()
        minimum_range: float | None = None
        first_met: float | None = None
        injection_origin = active[0].relative if active else 0.0
        for item in active:
            points.append((item.relative, item.hottest_control_c))
            cutoff = item.relative - window_s
            # Keep the last point at or before the cutoff. This makes coverage
            # conservative without requiring a packet at the exact boundary.
            while len(points) >= 2 and points[1][0] <= cutoff:
                points.popleft()
            if not points or points[0][0] > cutoff:
                continue
            values = [value for _, value in points]
            value_range = max(values) - min(values)
            minimum_range = value_range if minimum_range is None else min(minimum_range, value_range)
            if first_met is None and value_range <= range_limit_c:
                first_met = (
                    float(item.profile_elapsed_s)
                    if item.profile_elapsed_s is not None
                    else item.relative - injection_origin
                )
        diagnostics.append(StabilityDiagnostic(
            window_s=window_s,
            range_limit_c=range_limit_c,
            observed_duration_s=observed_duration,
            minimum_range_c=minimum_range,
            met=first_met is not None,
            first_met_elapsed_s=first_met,
        ))
    return diagnostics


def initial_target(fan_mode: str, config: dict) -> float:
    if fan_mode.upper() == "MAX":
        return float(config["initial_temperature_c"]["max"])
    return float(config["initial_temperature_c"]["normal"])


def initial_ready(reading: StatusReading, fan_mode: str, config: dict, stable: bool) -> bool:
    tolerance = float(config["initial_temperature_c"]["tolerance"])
    target = initial_target(fan_mode, config)
    return stable and abs(reading.hottest_control_c - target) <= tolerance


def historical_stress_endpoint_enabled(config: dict, criterion: str) -> bool:
    return bool(
        criterion in ("historical_time", "stable_temperature")
        and config.get("event_rules", {}).get("status_14_is_historical_stress_endpoint", True)
    )


def historical_stress_endpoint(readings: list[StatusReading], config: dict, criterion: str) -> StatusReading | None:
    if not historical_stress_endpoint_enabled(config, criterion):
        return None
    terminations = [item for item in readings if item.progress_status == 14]
    for termination in terminations:
        associated_thermal = next((
            item for item in readings
            if item.error_latched
            and item.error_code == 6
            and 0.0 <= item.relative - termination.relative <= 2.0
        ), None)
        if associated_thermal is not None:
            continue
        # Status 14 is the observed IA1 Other / historical stress endpoint.
        # Explicit internal code 6 remains Thermal Error and is not reclassified.
        if not termination.error_latched or termination.error_code != 6:
            return termination
    return None


def evaluate(readings: Iterable[StatusReading], profile: str, config: dict, stable: bool,
             initial_valid: bool = True, fan_mode: str = "MAX") -> Evaluation:
    values = list(readings)
    if not values:
        return Evaluation(
            "INCONCLUSIVE", "No thermal readings were decoded.", None, None, stable, initial_valid,
            "INCONCLUSIVE", "NOT ASSESSED", "No thermal readings were decoded.", "No injection evidence was available.",
        )

    max_control = max(item.hottest_control_c for item in values)
    elapsed_values = [item.profile_elapsed_s for item in values if item.profile_elapsed_s is not None]
    elapsed = max(elapsed_values) if elapsed_values else None
    profile_config = config["profiles"][profile]
    criterion = profile_config["criterion"]
    thermal_observed = next((item for item in values if item.error_latched and item.error_code == 6), None)
    error_termination = next((item for item in values if item.progress_status == 14), None)
    other_endpoint = historical_stress_endpoint(values, config, criterion)
    unidentified_error = error_termination is not None and thermal_observed is None and other_endpoint is None
    thermal = thermal_observed if config["event_rules"].get("thermal_error_always_stops_timer", True) else None
    electrical_status = "REVIEW" if other_endpoint is not None or unidentified_error else "NO ERROR OBSERVED"
    electrical_reason = (
        "IA1 Other interrupted the electrical output; this occurrence does not prove thermal stabilization."
        if other_endpoint is not None
        else "An AMPro error terminated injection, but its electrical label was not decoded."
        if unidentified_error
        else "No decoded electrical-output error was observed."
    )

    def finish(thermal_status: str, thermal_reason: str, effective: float | None = elapsed,
               stable_result: bool = stable, valid: bool = initial_valid) -> Evaluation:
        if thermal_status == "FAIL":
            overall = "FAIL"
        elif electrical_status == "REVIEW":
            overall = "REVIEW"
        else:
            overall = thermal_status
        combined = thermal_reason
        if electrical_status == "REVIEW":
            combined = f"{thermal_reason} Electrical output: {electrical_reason}"
        return Evaluation(
            overall, combined, effective, max_control, stable_result, valid,
            thermal_status, electrical_status, thermal_reason, electrical_reason,
        )

    if criterion == "monitor_only":
        return Evaluation(
            "MONITORING", "Free Profile is recording data without an automatic acceptance criterion.",
            elapsed, max_control, stable, initial_valid, "MONITORING", electrical_status,
            "No automatic thermal criterion is configured.", electrical_reason,
        )

    if not any(item.executing for item in values):
        return finish("INCONCLUSIVE", "No injection was observed in the recorded evidence.")

    if not initial_valid:
        detail = " A Thermal Error was observed, but it cannot determine acceptance without the initial condition." if thermal_observed else ""
        return finish("INCONCLUSIVE", "The required initial condition was not observed before injection." + detail, valid=False)

    reference_fan_mode = str(config.get("test_scope", {}).get("historical_reference_fan_mode", "MAX")).upper()
    if fan_mode.upper() != reference_fan_mode and profile != "custom":
        return finish(
            "INCONCLUSIVE",
            f"Fan Mode {fan_mode.title()} is a comparative route; the approved automatic criteria apply only to Fan Mode Max Power.",
        )

    if criterion == "historical_time":
        reference = effective_historical_reference_s(profile_config, fan_mode, config)
        endpoint = thermal or error_termination
        effective = endpoint.profile_elapsed_s if endpoint and endpoint.profile_elapsed_s is not None else elapsed
        if thermal and effective is not None and effective < reference:
            return finish("FAIL", f"Thermal Error occurred at {effective:.3f} s, before the {reference:.3f} s reference.", effective)
        if effective is not None and effective >= reference:
            suffix = " before IA1 Other interrupted the electrical output" if other_endpoint else ""
            return finish("PASS", f"The test set thermally sustained at least the {reference:.3f} s internal reference{suffix}.", effective)
        if other_endpoint is not None:
            observed = "unknown time" if effective is None else f"{effective:.3f} s"
            return finish("INCONCLUSIVE", f"IA1 Other interrupted injection at {observed}, before the {reference:.3f} s thermal reference was demonstrated.", effective)
        return finish("INCONCLUSIVE", f"The capture has not yet reached the {reference:.3f} s thermal reference.", effective)

    if criterion == "stable_temperature":
        approved_max = float(profile_config["approved_max_c"])
        investigate_max = float(profile_config["investigate_max_c"])
        stability_config = config.get("stability", {})
        stability_approved = bool(stability_config.get("window_approved", False) and stability_config.get("window_s") is not None)
        if max_control > investigate_max:
            return finish("FAIL", f"Temperature exceeded the {investigate_max:.1f} °C internal limit.", stable_result=False)
        if thermal and not stable:
            return finish("FAIL", "Thermal Error occurred before the approved stabilization rule was completed.", stable_result=False)
        if not stability_approved:
            return finish("INCONCLUSIVE", "No injection-stabilization rule is approved for this profile.", stable_result=False)
        if not stable:
            if other_endpoint is not None:
                return finish("INCONCLUSIVE", "IA1 Other interrupted injection before the approved stabilization rule was completed.", stable_result=False)
            return finish("INCONCLUSIVE", "Temperature has not yet met the approved stabilization rule.", stable_result=False)
        if max_control <= approved_max:
            return finish("PASS", f"Temperature stabilized at or below {approved_max:.1f} °C.")
        return finish("REVIEW", f"Temperature stabilized between {approved_max:.1f} and {investigate_max:.1f} °C.")

    if criterion == "reach_stability":
        if thermal:
            return finish("FAIL", "Thermal Error occurred before stabilization.", stable_result=False)
        if stable:
            return finish("PASS", "Temperature met the approved internal stabilization rule without Thermal Error.")
        if other_endpoint is not None:
            return finish("INCONCLUSIVE", "IA1 Other interrupted injection before stabilization was demonstrated.", stable_result=False)
        return finish("INCONCLUSIVE", "Temperature has not met the approved internal stabilization rule.", stable_result=False)

    return finish("INCONCLUSIVE", f"Unknown criterion: {criterion}")

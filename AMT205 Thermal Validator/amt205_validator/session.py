from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

from . import __version__
from .criteria import Evaluation, RollingStability, analyze_stability, evaluate, initial_target
from .protocol import (
    CONTROL_SENSORS,
    TEMPERATURE_FIELDS,
    PacketRecord,
    ProtocolEvent,
    StatusReading,
    decode_fan_mode,
    decode_state_duration,
    decode_status,
    decode_vector_command,
    is_clear_error_command,
)
from .workflow import WorkflowAdvisor, WorkflowStep


PROTOCOL_MAP_VERSION = "AMT205-V8-2026-09-01.7"


@dataclass(slots=True)
class SampleSnapshot:
    reading: StatusReading
    fan_mode: str
    stability_range_c: float | None
    stability_slope_c_per_min: float | None
    stable: bool
    initial_target_c: float
    initial_ready: bool
    initial_baseline_c: float | None
    initial_baseline_range_c: float | None
    initial_baseline_slope_c_per_min: float | None
    initial_baseline_observation_s: float
    historical_stress_endpoint: bool


class TestSession:
    def __init__(self, config: dict, metadata: dict, on_update: Callable[[SampleSnapshot], None] | None = None) -> None:
        self.config = config
        self.metadata = metadata
        self.on_update = on_update
        self.profile = metadata.get("profile", "current_50")
        self.expected_fan_mode = metadata.get("fan_mode", "MAX").upper()
        stability_config = config["stability"]
        configured_window = stability_config.get("window_s")
        window_approved = bool(stability_config.get("window_approved", False) and configured_window is not None)
        self.stability = RollingStability(
            window_s=float(configured_window) if window_approved else None,
            range_limit_c=float(stability_config["maximum_range_c"]),
            slope_limit_c_per_min=float(stability_config["maximum_abs_slope_c_per_min"]),
        )
        self.samples: list[SampleSnapshot] = []
        self.events: list[ProtocolEvent] = []
        self.vectors: list[dict] = []
        self.fan_mode = "NOT IDENTIFIED"
        self._last_execution: bool | None = None
        self._last_error: tuple[bool, int] | None = None
        self._last_progress: int | None = None
        self._last_fan: str | None = None
        self.workflow = WorkflowAdvisor(config, metadata)
        self.workflow_step: WorkflowStep = self.workflow.current_step

    def add_event(self, packet: PacketRecord, kind: str, severity: str, message: str, details: dict | None = None) -> None:
        self.events.append(
            ProtocolEvent(packet.frame, packet.relative, packet.timestamp_iso, kind, severity, message, details=details or {})
        )

    def add_monitor_event(
        self,
        kind: str,
        severity: str,
        message: str,
        relative_s: float,
        details: dict | None = None,
    ) -> ProtocolEvent:
        event = ProtocolEvent(
            frame=0,
            relative=relative_s,
            timestamp_iso=datetime.now().astimezone().isoformat(timespec="milliseconds"),
            kind=kind,
            severity=severity,
            message=message,
            source="monitor",
            details=details or {},
        )
        self.events.append(event)
        return event

    def process(self, packet: PacketRecord) -> None:
        fan = decode_fan_mode(packet)
        if fan is not None:
            self.fan_mode = fan
            if fan != self._last_fan:
                severity = "INFO" if fan == self.expected_fan_mode else "WARNING"
                fan_label = "Max Power" if fan == "MAX" else "Normal"
                self.add_event(packet, "fan_mode", severity, f"Fan Mode identified: {fan_label}.", {"fan_mode": fan})
                self._last_fan = fan

        vector = decode_vector_command(packet)
        if vector is not None:
            item = {"frame": packet.frame, "relative_s": packet.relative, "timestamp_iso": packet.timestamp_iso,
                    "state_index": vector.state_index, "channels": vector.channels}
            self.vectors.append(item)
            nonzero = [channel for channel in vector.channels if channel["amplitude_raw"] != 0]
            summary = ", ".join(f"CH{ch['channel']}={ch['amplitude']:.3f}" for ch in nonzero) or "all channels at zero"
            self.add_event(packet, "vector", "INFO", f"Injection vector, State {vector.state_index}: {summary}.", item)

        state = decode_state_duration(packet)
        if state is not None:
            self.add_event(packet, "state_configuration", "INFO", f"State {state['state_index']} configured for {state['duration_s']:.3f} s.", state)

        if is_clear_error_command(packet):
            self.add_event(packet, "clear_error", "INFO", "Clear Error command observed.")

        reading = decode_status(packet)
        if reading is None:
            return
        if reading.executing and self._last_execution is not True:
            self.stability.reset()
        if reading.executing:
            self.stability.add(reading.relative, reading.hottest_control_c)
        fan_for_criterion = self.fan_mode if self.fan_mode in ("NORMAL", "MAX") else self.expected_fan_mode
        previous_workflow_code = self.workflow_step.code
        endpoint_was_observed = self.workflow.historical_stress_endpoint_observed
        self.workflow_step = self.workflow.update(reading, fan_for_criterion)
        endpoint_just_confirmed = self.workflow.historical_stress_endpoint_observed and not endpoint_was_observed
        snapshot = SampleSnapshot(
            reading=reading,
            fan_mode=self.fan_mode,
            stability_range_c=self.stability.range_c,
            stability_slope_c_per_min=self.stability.slope_c_per_min,
            stable=self.stability.stable,
            initial_target_c=initial_target(fan_for_criterion, self.config),
            initial_ready=self.workflow.initial_confirmed,
            initial_baseline_c=self.workflow.initial_baseline_c,
            initial_baseline_range_c=self.workflow.initial_baseline_range_c,
            initial_baseline_slope_c_per_min=self.workflow.initial_baseline_slope_c_per_min,
            initial_baseline_observation_s=self.workflow.initial_baseline_observation_s,
            historical_stress_endpoint=self.workflow.historical_stress_endpoint_observed,
        )
        self.samples.append(snapshot)

        if self.workflow_step.code != previous_workflow_code:
            self.add_event(
                packet,
                "workflow_step",
                self.workflow_step.level,
                f"Next Step: {self.workflow_step.instruction}",
                self.workflow_step.to_dict(),
            )

        if endpoint_just_confirmed:
            self.add_event(
                packet,
                "electrical_output_endpoint",
                "WARNING",
                "IA1 Other electrical-output endpoint confirmed by progress status 14.",
                {"status": 14, "operator_confirmed_label": "IA1 Other"},
            )

        if reading.executing != self._last_execution:
            self.add_event(packet, "execution", "INFO", "Injection active." if reading.executing else "Injection inactive.")
            self._last_execution = reading.executing
        error_state = (reading.error_latched, reading.error_code)
        if error_state != self._last_error:
            if reading.error_latched:
                severity = "FAIL" if reading.error_code == 6 else "WARNING"
                if reading.error_code == 6:
                    message = "Thermal Error active, internal code 6."
                else:
                    message = f"AMT205 internal error active, numeric code {reading.error_code}; AMPro error text is not decoded."
                self.add_event(packet, "latched_error", severity, message, {"code": reading.error_code})
            elif self._last_error and self._last_error[0]:
                self.add_event(packet, "error_cleared", "INFO", "Internal Error cleared.")
            self._last_error = error_state
        if reading.progress_status is not None and reading.progress_status != self._last_progress:
            thermal = reading.error_latched and reading.error_code == 6
            severity = "FAIL" if thermal else "WARNING" if reading.progress_status == 14 else "INFO"
            message = f"Progress status: {reading.progress_status}"
            details = {"status": reading.progress_status, "elapsed_s": reading.profile_elapsed_s}
            if reading.progress_status == 14:
                elapsed = "unknown" if reading.profile_elapsed_s is None else f"{reading.profile_elapsed_s:.6f} s"
                criterion = self.config["profiles"][self.profile].get("criterion", "monitor_only")
                historical_endpoint = bool(
                    self.config.get("event_rules", {}).get("status_14_is_historical_stress_endpoint", True)
                    and criterion in ("historical_time", "stable_temperature")
                    and not thermal
                )
                if historical_endpoint:
                    message = f"IA1 Other electrical-output endpoint at {elapsed}; AMPro text confirmed by the operator."
                    details["classification"] = "ia1_other_electrical_endpoint"
                    details["operator_confirmed_label"] = "IA1 Other"
                elif thermal:
                    message = f"Thermal termination at {elapsed}."
                    details["classification"] = "thermal_error"
                else:
                    message = f"Injection terminated with error at {elapsed}; progress status 14 does not identify the error type."
                    details["classification"] = "unidentified_error_termination"
            self.add_event(packet, "progress", severity, message, details)
            self._last_progress = reading.progress_status
        if self.on_update:
            self.on_update(snapshot)

    def evaluation(self) -> Evaluation:
        first_execution = next((index for index, item in enumerate(self.samples) if item.reading.executing), None)
        if first_execution is None:
            initial_valid = self.workflow.initial_confirmed
        else:
            initial_valid = not self.workflow.invalid_start and any(
                item.initial_ready for item in self.samples[: first_execution + 1]
            )
        return evaluate(
            (item.reading for item in self.samples), self.profile, self.config,
            self.stability.stable, initial_valid, self.expected_fan_mode,
        )

    def save(self, output_dir: Path, source_pcap: Path | None = None) -> dict[str, Path]:
        output_dir.mkdir(parents=True, exist_ok=True)
        sample_path = output_dir / "readings.csv"
        event_path = output_dir / "events.csv"
        vector_path = output_dir / "vectors.json"
        metadata_path = output_dir / "metadata.json"

        temperature_names = [name for name, _ in TEMPERATURE_FIELDS]
        sample_fields = [
            "timestamp_iso", "relative_s", "frame", "fan_mode", "executing", "progress_status", "profile_elapsed_s",
            *temperature_names, "hottest_control_name", "hottest_control_c", "hottest_internal_name", "hottest_internal_c",
            "stability_range_c", "stability_slope_c_per_min", "stable", "initial_target_c", "initial_ready", "initial_baseline_c",
            "initial_baseline_range_c", "initial_baseline_slope_c_per_min", "initial_baseline_observation_s",
            "historical_stress_endpoint", "error_latched", "error_code",
        ]
        with sample_path.open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=sample_fields, delimiter=";")
            writer.writeheader()
            for item in self.samples:
                reading = item.reading
                row = {
                    "timestamp_iso": reading.timestamp_iso, "relative_s": f"{reading.relative:.6f}", "frame": reading.frame,
                    "fan_mode": item.fan_mode, "executing": int(reading.executing), "progress_status": reading.progress_status,
                    "profile_elapsed_s": "" if reading.profile_elapsed_s is None else f"{reading.profile_elapsed_s:.6f}",
                    **{name: f"{reading.temperatures[name]:.1f}" for name in temperature_names},
                    "hottest_control_name": reading.hottest_control_name, "hottest_control_c": f"{reading.hottest_control_c:.1f}",
                    "hottest_internal_name": reading.hottest_internal_name, "hottest_internal_c": f"{reading.hottest_internal_c:.1f}",
                    "stability_range_c": "" if item.stability_range_c is None else f"{item.stability_range_c:.2f}",
                    "stability_slope_c_per_min": "" if item.stability_slope_c_per_min is None else f"{item.stability_slope_c_per_min:.4f}",
                    "stable": int(item.stable), "initial_target_c": f"{item.initial_target_c:.1f}",
                    "initial_ready": int(item.initial_ready),
                    "initial_baseline_c": "" if item.initial_baseline_c is None else f"{item.initial_baseline_c:.2f}",
                    "initial_baseline_range_c": "" if item.initial_baseline_range_c is None else f"{item.initial_baseline_range_c:.2f}",
                    "initial_baseline_slope_c_per_min": "" if item.initial_baseline_slope_c_per_min is None else f"{item.initial_baseline_slope_c_per_min:.4f}",
                    "initial_baseline_observation_s": f"{item.initial_baseline_observation_s:.3f}",
                    "historical_stress_endpoint": int(item.historical_stress_endpoint),
                    "error_latched": int(reading.error_latched), "error_code": reading.error_code,
                }
                writer.writerow(row)

        with event_path.open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=["timestamp_iso", "relative_s", "frame", "kind", "severity", "message", "source", "details"], delimiter=";")
            writer.writeheader()
            for event in self.events:
                row = asdict(event)
                row["relative_s"] = row.pop("relative")
                row["details"] = json.dumps(row["details"], ensure_ascii=False, separators=(",", ":"))
                writer.writerow(row)

        vector_path.write_text(json.dumps(self.vectors, ensure_ascii=False, indent=2), encoding="utf-8")
        result = self.evaluation()
        stability_config = self.config.get("stability", {})
        diagnostics = analyze_stability(
            (item.reading for item in self.samples),
            stability_config.get("diagnostic_windows_s", (30.0, 60.0, 120.0)),
            float(stability_config.get("maximum_range_c", 1.0)),
        )
        source_hash = None
        if source_pcap and source_pcap.exists():
            digest = hashlib.sha256()
            with source_pcap.open("rb") as handle:
                for block in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(block)
            source_hash = digest.hexdigest()
        metadata = {
            "schema_version": 2,
            "application_version": __version__,
            "protocol_map_version": PROTOCOL_MAP_VERSION,
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "session": self.metadata,
            "configuration": self.config,
            "capture": {
                "source": str(source_pcap) if source_pcap else None,
                "sha256": source_hash,
                "sample_count": len(self.samples),
                "event_count": len(self.events),
                "first_timestamp": self.samples[0].reading.timestamp_iso if self.samples else None,
                "last_timestamp": self.samples[-1].reading.timestamp_iso if self.samples else None,
            },
            "evaluation": asdict(result),
            "stability_diagnostics": [asdict(item) for item in diagnostics],
            "workflow": self.workflow.summary(),
            "limitations": [
                "CPU Temperature is not mapped in the protocol and is excluded from hottest-sensor calculations.",
                "Cooling and Smart Delay (Turn On/Off Fan) do not yet have confirmed protocol fields; their settings are recorded in the report.",
                "Progress status 14 is recorded as the operator-confirmed IA1 Other electrical endpoint; it does not by itself prove thermal stabilization or satisfy a thermal time reference.",
                "The internally approved injection-stability rule is 120 s with at most 1.0 °C range and 0.20 °C/min absolute trend; diagnostic windows remain informational.",
                "Initial stabilization uses the selected Fan Mode target (25 °C Max Power or 33 °C Normal) and remains an operational readiness check, not an injection-acceptance rule.",
                "Thermal recovery records return to the selected Fan Mode target after injection and does not alter the injection acceptance result.",
                "Automatic acceptance is conclusive only when the selected profile has an acceptance criterion and the required evidence was observed.",
            ],
        }
        metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"samples": sample_path, "events": event_path, "vectors": vector_path, "metadata": metadata_path}


def default_session_name() -> str:
    return datetime.now().strftime("AMT205_%Y%m%d_%H%M%S")

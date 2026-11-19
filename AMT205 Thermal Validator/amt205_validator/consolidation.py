from __future__ import annotations

import bisect
import csv
import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path


def _float(value) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _integer(value) -> int | None:
    number = _float(value)
    return None if number is None else int(number)


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle, delimiter=";"))


def discover_session_folders(root: Path) -> list[Path]:
    """Return session folders containing the standard evidence bundle."""
    root = root.expanduser().resolve()
    if not root.exists() or not root.is_dir():
        return []
    candidates: set[Path] = set()
    direct = root / "evidence"
    if (direct / "metadata.json").is_file() and (direct / "readings.csv").is_file():
        candidates.add(root)
    for metadata_path in root.rglob("metadata.json"):
        if metadata_path.parent.name != "evidence":
            continue
        session_dir = metadata_path.parent.parent
        try:
            relative_parts = session_dir.relative_to(root).parts
        except ValueError:
            continue
        if any(part.startswith(".") for part in relative_parts):
            continue
        if (metadata_path.parent / "readings.csv").is_file():
            candidates.add(session_dir.resolve())
    return sorted(candidates, key=lambda item: item.name.casefold())


def _profile_details(metadata: dict, profile_key_override: str | None = None) -> tuple[str, str, dict]:
    session = metadata.get("session", {})
    configuration = metadata.get("configuration", {})
    profile_key = str(profile_key_override or session.get("profile") or "unknown")
    profile = configuration.get("profiles", {}).get(profile_key, {})
    profile_label = str(profile.get("label") or profile_key.replace("_", " ").title())
    short_labels = {
        "current_25": "Current 25%",
        "current_50": "Current 50%",
        "current_75": "Current 75%",
        "current_100": "Current 100%",
        "voltage_300": "Voltage 6×300 V",
    }
    short_label = short_labels.get(profile_key)
    if short_label is None:
        match = re.search(r"Power Control\s*(\d+)%", profile_label, re.IGNORECASE)
        short_label = f"Current {match.group(1)}%" if match else profile_label
    return profile_key, short_label, profile


def read_session_summary(session_dir: Path) -> dict:
    session_dir = session_dir.resolve()
    metadata_path = session_dir / "evidence" / "metadata.json"
    readings_path = session_dir / "evidence" / "readings.csv"
    if not metadata_path.is_file() or not readings_path.is_file():
        raise ValueError(f"Not a valid AMT205 session: {session_dir}")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    corrections_path = session_dir / "evidence" / "corrections.json"
    corrections = json.loads(corrections_path.read_text(encoding="utf-8")) if corrections_path.is_file() else {}
    session = metadata.get("session", {})
    configuration = metadata.get("configuration", {})
    evaluation = metadata.get("evaluation", {})
    workflow = metadata.get("workflow", {})
    recorded_profile_key = str(session.get("profile") or "unknown")
    profile_correction = corrections.get("corrections", {}).get("profile", {})
    effective_profile_key = str(profile_correction.get("effective") or recorded_profile_key)
    profile_key, profile_short_base, profile = _profile_details(metadata, effective_profile_key)
    _recorded_key, recorded_profile_short, _recorded_profile = _profile_details(metadata, recorded_profile_key)
    profile_corrected = profile_key != recorded_profile_key
    profile_short = profile_short_base + (" (corrected)" if profile_corrected else "")
    recorded_fan_mode = str(session.get("fan_mode") or "NOT RECORDED").upper()
    fan_correction = corrections.get("corrections", {}).get("fan_mode", {})
    fan_mode = str(fan_correction.get("effective") or recorded_fan_mode).upper()
    fan_mode_corrected = fan_mode != recorded_fan_mode
    reference_s = _float(profile.get("historical_time_s"))
    observed_s = _float(evaluation.get("elapsed_s"))
    margin_percent = None
    if observed_s is not None and reference_s not in (None, 0):
        margin_percent = round((observed_s / reference_s - 1.0) * 100.0, 6)
    quantitative_result = "COMPARATIVE" if fan_mode != "MAX" else "NOT ASSESSED"
    if fan_mode == "MAX" and observed_s is not None and reference_s is not None:
        quantitative_result = "PASS" if observed_s >= reference_s else "BELOW REFERENCE"
    baseline = workflow.get("initial_baseline", {})
    recovery = workflow.get("thermal_recovery", {})
    timestamp = metadata.get("capture", {}).get("first_timestamp") or metadata.get("generated_at") or ""
    end_timestamp = metadata.get("capture", {}).get("last_timestamp") or timestamp
    repetition = _integer(session.get("repetition")) or 1
    return {
        "folder": str(session_dir),
        "session_name": session_dir.name,
        "timestamp": str(timestamp),
        "end_timestamp": str(end_timestamp),
        "serial_number": str(session.get("serial_number") or "Not provided"),
        "model": str(session.get("model") or configuration.get("equipment", {}).get("model") or "Not recorded"),
        "firmware": str(session.get("firmware") or configuration.get("equipment", {}).get("firmware") or "Not recorded"),
        "profile_key": profile_key,
        "profile": str(profile.get("label") or profile_short_base) + (" (corrected)" if profile_corrected else ""),
        "profile_short": profile_short,
        "recorded_profile_key": recorded_profile_key,
        "recorded_profile_short": recorded_profile_short,
        "profile_corrected": profile_corrected,
        "profile_correction_reason": str(profile_correction.get("reason") or ""),
        "repetition": repetition,
        "fan_mode": ("Max Power" if fan_mode == "MAX" else fan_mode.title()) + (" (corrected)" if fan_mode_corrected else ""),
        "recorded_fan_mode": "Max Power" if recorded_fan_mode == "MAX" else recorded_fan_mode.title(),
        "fan_mode_corrected": fan_mode_corrected,
        "fan_mode_correction_reason": str(fan_correction.get("reason") or ""),
        "frequency_hz": _float(session.get("injection_frequency_hz") or configuration.get("test_scope", {}).get("injection_frequency_hz")),
        "ambient_c": _float(session.get("ambient_c")),
        "initial_c": _float(baseline.get("temperature_c")),
        "reference_s": reference_s,
        "observed_s": observed_s,
        "margin_percent": margin_percent,
        "max_control_c": _float(evaluation.get("max_control_c")),
        "quantitative_result": quantitative_result,
        "thermal_result": str(evaluation.get("thermal_status") or "NOT ASSESSED"),
        "electrical_result": str(evaluation.get("electrical_status") or "NOT ASSESSED"),
        "overall_result": str(evaluation.get("status") or "INCONCLUSIVE"),
        "rationale": str(evaluation.get("reason") or ""),
        "recovery_target_c": _float(recovery.get("target_c")),
        "recovery_time_s": _float(recovery.get("time_to_target_s")),
        "recovery_end_c": _float(recovery.get("end_temperature_c")),
        "recovery_result": (
            "REACHED" if recovery.get("reached") is True
            else "NOT REACHED" if recovery.get("timed_out") is True
            else "NOT RECORDED"
        ),
    }


def _timestamp(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _injection_points(rows: list[dict[str, str]]) -> list[tuple[float, float, float | None]]:
    available: list[tuple[float, float, float | None, int | None]] = []
    for row in rows:
        elapsed = _float(row.get("profile_elapsed_s"))
        control = _float(row.get("hottest_control_c"))
        internal = _float(row.get("hottest_internal_c"))
        if elapsed is None or control is None:
            continue
        available.append((elapsed, control, internal, _integer(row.get("executing"))))
    active = [item for item in available if item[3] == 1]
    selected = active or available
    deduplicated: dict[float, tuple[float, float | None]] = {}
    for elapsed, control, internal, _executing in selected:
        deduplicated[elapsed] = (control, internal)
    return [(elapsed, *deduplicated[elapsed]) for elapsed in sorted(deduplicated)]


def _interpolate(points: list[tuple[float, float, float | None]], elapsed: float) -> float | None:
    if not points or elapsed < points[0][0] or elapsed > points[-1][0]:
        return None
    times = [point[0] for point in points]
    index = bisect.bisect_left(times, elapsed)
    if index < len(points) and math.isclose(points[index][0], elapsed, abs_tol=1e-9):
        return points[index][1]
    if index == 0 or index >= len(points):
        return None
    left_time, left_value, _ = points[index - 1]
    right_time, right_value, _ = points[index]
    fraction = (elapsed - left_time) / (right_time - left_time)
    return left_value + (right_value - left_value) * fraction


def build_compilation_payload(session_dirs: list[Path], source_root: Path | None = None) -> dict:
    loaded = []
    for session_dir in session_dirs:
        summary = read_session_summary(session_dir)
        rows = _read_csv(session_dir / "evidence" / "readings.csv")
        points = _injection_points(rows)
        if not points:
            raise ValueError(f"Session has no usable injection temperature readings: {session_dir}")
        loaded.append((summary, points))
    if not loaded:
        raise ValueError("No valid AMT205 sessions were selected.")
    loaded.sort(key=lambda item: (item[0]["timestamp"], item[0]["session_name"]))

    label_counts: dict[str, int] = {}
    sessions: list[dict] = []
    all_readings: list[dict] = []
    labelled_points: list[tuple[str, list[tuple[float, float, float | None]]]] = []
    previous_end: datetime | None = None
    for cycle_number, (summary, points) in enumerate(loaded, start=1):
        start = _timestamp(summary["timestamp"])
        interval_s = None
        if start is not None and previous_end is not None:
            interval_s = max(0.0, (start - previous_end).total_seconds())
        end = _timestamp(summary["end_timestamp"])
        previous_end = end or start or previous_end
        base_label = f"{summary['profile_short']} — Rep. {summary['repetition']}"
        label_counts[base_label] = label_counts.get(base_label, 0) + 1
        suffix = f" ({label_counts[base_label]})" if label_counts[base_label] > 1 else ""
        label = base_label + suffix
        summary = {
            **summary,
            "chart_label": label,
            "cycle_number": cycle_number,
            "interval_since_previous_s": interval_s,
        }
        sessions.append(summary)
        labelled_points.append((label, points))
        for elapsed, control, internal in points:
            all_readings.append({
                "session": label,
                "session_name": summary["session_name"],
                "injection_time_s": elapsed,
                "hottest_control_c": control,
                "hottest_internal_c": internal,
            })

    maximum_duration = max(points[-1][0] for _label, points in labelled_points)
    # Keep the comparison readable in both Excel and the exported PNG.  Around
    # thirty common-time samples retain the overall thermal curve without
    # crowding the horizontal axis with hundreds of labels.
    step = max(1.0, math.ceil(maximum_duration / 30.0))
    chart_times = [round(index * step, 6) for index in range(int(maximum_duration // step) + 1)]
    chart_series = [
        {"label": label, "values": [_interpolate(points, elapsed) for elapsed in chart_times]}
        for label, points in labelled_points
    ]
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_root": str(source_root.resolve()) if source_root is not None else "",
        "sessions": sessions,
        "chart": {"times": chart_times, "series": chart_series},
        "readings": all_readings,
    }

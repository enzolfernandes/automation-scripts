from __future__ import annotations

from pathlib import Path
from typing import Any

import xlsxwriter


NAVY = "#17324D"
BLUE = "#2878B5"
PALE_BLUE = "#EAF3F8"
PALE_GREEN = "#E8F4EE"
PALE_AMBER = "#FFF3D6"
LIGHT = "#F5F7F9"
WHITE = "#FFFFFF"


def _formats(workbook: xlsxwriter.Workbook) -> dict[str, Any]:
    return {
        "title": workbook.add_format({"bold": True, "font_color": WHITE, "bg_color": NAVY, "font_size": 18, "valign": "vcenter"}),
        "subtitle": workbook.add_format({"italic": True, "font_color": "#5C6B73", "bg_color": PALE_BLUE, "text_wrap": True}),
        "header": workbook.add_format({"bold": True, "font_color": WHITE, "bg_color": BLUE, "text_wrap": True, "valign": "vcenter", "border": 1, "border_color": "#D9E1E6"}),
        "cell": workbook.add_format({"border": 1, "border_color": "#D9E1E6"}),
        "number": workbook.add_format({"border": 1, "border_color": "#D9E1E6", "num_format": "0.000"}),
        "label": workbook.add_format({"bold": True, "font_color": NAVY, "bg_color": PALE_BLUE, "border": 1, "border_color": "#B8C7D1"}),
        "pass": workbook.add_format({"bold": True, "font_color": "#1B7F79", "bg_color": PALE_GREEN, "align": "center", "valign": "vcenter"}),
        "review": workbook.add_format({"bold": True, "font_color": "#8A5A00", "bg_color": PALE_AMBER, "align": "center", "valign": "vcenter"}),
        "neutral": workbook.add_format({"bold": True, "font_color": NAVY, "bg_color": LIGHT, "align": "center", "valign": "vcenter"}),
        "section": workbook.add_format({"bold": True, "font_color": WHITE, "bg_color": BLUE}),
        "wrap": workbook.add_format({"text_wrap": True, "valign": "top", "bg_color": LIGHT}),
    }


def _title(sheet, formats, title: str, subtitle: str, end_col: int) -> None:
    sheet.hide_gridlines(2)
    sheet.merge_range(0, 0, 1, end_col, title, formats["title"])
    sheet.merge_range(2, 0, 2, end_col, subtitle, formats["subtitle"])
    sheet.set_row(0, 25)
    sheet.set_row(1, 25)
    sheet.set_row(2, 30)


def _write_table(sheet, formats, row: int, headers: list[str], rows: list[list[Any]], widths: list[int] | None = None) -> None:
    for column, header in enumerate(headers):
        sheet.write(row, column, header, formats["header"])
    for row_offset, values in enumerate(rows, start=1):
        for column, value in enumerate(values):
            fmt = formats["number"] if isinstance(value, (int, float)) and not isinstance(value, bool) else formats["cell"]
            sheet.write(row + row_offset, column, value, fmt)
    sheet.autofilter(row, 0, row + len(rows), len(headers) - 1)
    sheet.freeze_panes(row + 1, 0)
    if widths:
        for column, width in enumerate(widths):
            sheet.set_column(column, column, width)


def _status_format(formats, status: str):
    return formats["pass"] if status in {"PASS", "NO ERROR OBSERVED", "REACHED"} else formats["review"] if status in {"REVIEW", "FAIL"} else formats["neutral"]


def create_report(payload: dict, output_path: Path) -> Path:
    metadata = payload["metadata"]
    samples = payload.get("samples", [])
    events = payload.get("events", [])
    session = metadata.get("session", {})
    config = metadata.get("configuration", {})
    evaluation = metadata.get("evaluation", {})
    workflow = metadata.get("workflow", {})
    recovery = workflow.get("thermal_recovery", {})
    baseline = workflow.get("initial_baseline", {})
    profile_key = session.get("profile", "custom")
    profile = config.get("profiles", {}).get(profile_key, {})
    profile_label = profile.get("label", profile_key)
    fan = "Max Power" if session.get("fan_mode") == "MAX" else "Normal"
    reference = profile.get("historical_time_s")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    workbook = xlsxwriter.Workbook(str(output_path))
    formats = _formats(workbook)
    summary = workbook.add_worksheet("Summary")
    results = workbook.add_worksheet("Results")
    readings = workbook.add_worksheet("Readings")
    event_sheet = workbook.add_worksheet("Events")
    charts = workbook.add_worksheet("Charts")
    configuration = workbook.add_worksheet("Configuration")
    trace = workbook.add_worksheet("Traceability")

    _title(summary, formats, "AMT205 Thermal Validation Report", "Report generated from the preserved passive-monitor evidence.", 7)
    summary_rows = [
        ["Equipment", session.get("model", "Vebko AMT205 V8")], ["Firmware", session.get("firmware", "")],
        ["Serial Number", session.get("serial_number", "")], ["Profile", profile_label], ["Fan Mode", fan],
        ["Initial Baseline", f"{baseline.get('temperature_c', 0):.1f} °C" if baseline.get("confirmed") else "Not confirmed"],
        ["Thermal Recovery", f"{recovery.get('time_to_target_s', 0):.1f} s" if recovery.get("reached") else "Not completed"],
        ["Max. Control", evaluation.get("max_control_c")],
    ]
    for index, (label, value) in enumerate(summary_rows, start=4):
        summary.write(index, 0, label, formats["label"]); summary.write(index, 1, value, formats["cell"])
    summary.merge_range("D5:H5", "RESULT", formats["title"])
    overall = evaluation.get("status", "INCONCLUSIVE")
    summary.merge_range("D6:H8", overall, _status_format(formats, overall))
    summary.merge_range("D9:E10", "Thermal Result", formats["label"])
    summary.merge_range("F9:H10", evaluation.get("thermal_status", "INCONCLUSIVE"), _status_format(formats, evaluation.get("thermal_status", "INCONCLUSIVE")))
    summary.merge_range("D11:E12", "Electrical Output Result", formats["label"])
    summary.merge_range("F11:H12", evaluation.get("electrical_status", "NO ERROR OBSERVED"), _status_format(formats, evaluation.get("electrical_status", "")))
    summary.merge_range("A15:H15", "Automatic Conclusion", formats["section"])
    summary.merge_range("A16:H18", evaluation.get("reason", ""), formats["wrap"])
    summary.merge_range("A21:H21", "Operator Notes", formats["section"])
    summary.merge_range("A22:H25", session.get("notes") or "Free field for operator observations.", formats["wrap"])
    summary.set_column("A:A", 22); summary.set_column("B:B", 40); summary.set_column("C:C", 3); summary.set_column("D:H", 16)

    _title(results, formats, "Test Result", "Thermal conformity and electrical-output occurrences are shown separately.", 12)
    result_headers = ["Profile", "Fan Mode", "Rep.", "Criterion", "Observed Time (s)", "Reference Time (s)", "Max. Control (°C)", "Initial Valid", "Stable", "Thermal Result", "Electrical Result", "Overall Result", "Rationale"]
    _write_table(results, formats, 4, result_headers, [[profile_label, fan, session.get("repetition", 1), profile.get("criterion", ""), evaluation.get("elapsed_s"), reference, evaluation.get("max_control_c"), int(bool(evaluation.get("initial_valid"))), int(bool(evaluation.get("stable"))), evaluation.get("thermal_status"), evaluation.get("electrical_status"), overall, evaluation.get("reason", "")]], [34, 14, 8, 24, 16, 16, 16, 12, 10, 16, 20, 16, 60])
    statuses = [evaluation.get("thermal_status"), evaluation.get("electrical_status"), overall]
    for column, status in zip((9, 10, 11), statuses):
        results.write(5, column, status, _status_format(formats, status))

    reading_headers = list(samples[0]) if samples else ["timestamp_iso", "relative_s", "frame"]
    reading_rows = [[sample.get(header) for header in reading_headers] for sample in samples]
    _title(readings, formats, "Decoded Thermal Readings", "Internal AMT205 sensor data; CPU Temperature is excluded from control calculations.", max(2, len(reading_headers) - 1))
    _write_table(readings, formats, 4, reading_headers, reading_rows, [24 if h == "timestamp_iso" else 20 if len(h) > 18 else 14 for h in reading_headers])

    event_headers = list(events[0]) if events else ["timestamp_iso", "relative_s", "frame", "kind", "severity", "message", "source", "details"]
    event_rows = [[event.get(header) for header in event_headers] for event in events]
    _title(event_sheet, formats, "Events and Transitions", "Events observed in packets; the validator does not transmit commands.", max(7, len(event_headers) - 1))
    _write_table(event_sheet, formats, 4, event_headers, event_rows, [24, 13, 9, 22, 12, 55, 14, 38][:len(event_headers)])

    _title(charts, formats, "Thermal Trend", "Hottest V/I sensor and hottest mapped internal sensor.", 13)
    chart_headers = ["Relative Time (s)", "Max. V/I (°C)", "Max. Internal (°C)"]
    chart_step = max(1, (len(samples) + 199) // 200)
    chart_samples = samples[::chart_step]
    if samples and chart_samples[-1] is not samples[-1]: chart_samples.append(samples[-1])
    _write_table(charts, formats, 4, chart_headers, [[s.get("relative_s"), s.get("hottest_control_c"), s.get("hottest_internal_c")] for s in chart_samples], [18, 18, 20])
    if chart_samples:
        chart = workbook.add_chart({"type": "line"})
        for column, color in ((1, "#2878B5"), (2, "#1B7F79")):
            chart.add_series({"name": ["Charts", 4, column], "categories": ["Charts", 5, 0, 4 + len(chart_samples), 0], "values": ["Charts", 5, column, 4 + len(chart_samples), column], "line": {"color": color, "width": 2}})
        chart.set_title({"name": "Thermal Trend During Test (°C)"}); chart.set_x_axis({"name": "Relative Time (s)"}); chart.set_y_axis({"name": "Temperature (°C)", "num_format": "0.0"}); chart.set_legend({"position": "bottom"})
        charts.insert_chart("E5", chart, {"x_scale": 1.5, "y_scale": 1.4})

    _title(configuration, formats, "Applied Configuration", "Values recorded for this evaluation.", 3)
    config_rows = [["Profile", profile_label, "Session"], ["Fan Mode", fan, "Session"], ["Injection Frequency", session.get("injection_frequency_hz"), "Hz"], ["Historical Reference", reference, "s"], ["Initial Target Max Power", config.get("initial_temperature_c", {}).get("max"), "°C"], ["Initial Target Normal", config.get("initial_temperature_c", {}).get("normal"), "°C"], ["Recovery Hold", config.get("thermal_recovery", {}).get("hold_s"), "s"]]
    _write_table(configuration, formats, 4, ["Field", "Value", "Unit"], config_rows, [38, 38, 18])

    _title(trace, formats, "Traceability and Limitations", "Evidence identification and known limitations.", 7)
    capture = metadata.get("capture", {})
    trace_rows = [["Application Version", metadata.get("application_version")], ["Protocol Map Version", metadata.get("protocol_map_version")], ["Source File", capture.get("source")], ["Source SHA-256", capture.get("sha256")], ["Reading Count", capture.get("sample_count")], ["Event Count", capture.get("event_count")], ["Recovery Result", "Reached" if recovery.get("reached") else "Not completed"]]
    _write_table(trace, formats, 4, ["Field", "Value"], trace_rows, [35, 80])
    row = 13
    trace.merge_range(row, 0, row, 7, "Known Limitations", formats["section"])
    for limitation in metadata.get("limitations", []): row += 1; trace.merge_range(row, 0, row, 7, f"• {limitation}", formats["wrap"])

    workbook.close()
    return output_path


def create_compilation(payload: dict, output_path: Path) -> Path:
    sessions = payload.get("sessions", [])
    chart_times = payload.get("chart", {}).get("times", [])
    chart_series = payload.get("chart", {}).get("series", [])
    readings = payload.get("readings", [])
    output_path.parent.mkdir(parents=True, exist_ok=True)
    workbook = xlsxwriter.Workbook(str(output_path))
    formats = _formats(workbook)
    summary = workbook.add_worksheet("Summary"); comparison = workbook.add_worksheet("Comparison"); compiled = workbook.add_worksheet("Compiled Readings")
    _title(summary, formats, "AMT205 Thermal Session Compilation", f"{len(sessions)} successive thermal cycle(s).", 19)
    headers = ["Cycle", "Session", "Date / Time", "Interval (s)", "Serial Number", "Profile", "Rep.", "Fan Mode", "Frequency (Hz)", "Initial (°C)", "Reference (s)", "Observed (s)", "Margin (%)", "Max. V/I (°C)", "Recovery Target (°C)", "Recovery Time (s)", "Recovery End (°C)", "Recovery Result", "Quantitative Result", "Recorded Result"]
    rows = [[s.get(k) for k in ("cycle_number", "session_name", "timestamp", "interval_since_previous_s", "serial_number", "profile_short", "repetition", "fan_mode", "frequency_hz", "initial_c", "reference_s", "observed_s", "margin_percent", "max_control_c", "recovery_target_c", "recovery_time_s", "recovery_end_c", "recovery_result", "quantitative_result", "overall_result")] for s in sessions]
    _write_table(summary, formats, 4, headers, rows, [8, 25, 23, 18, 17, 20, 8, 19, 16, 13, 15, 15, 13, 15, 18, 18, 18, 18, 20, 18])
    _title(comparison, formats, "Thermal Comparison", "Hottest V/I sensor during injection on a common time scale.", 9)
    comparison_headers = ["Injection Time (s)"] + [series.get("label") for series in chart_series]
    comparison_rows = [[elapsed] + [(series.get("values") or [None] * len(chart_times))[index] for series in chart_series] for index, elapsed in enumerate(chart_times)]
    _write_table(comparison, formats, 4, comparison_headers, comparison_rows, [18] * len(comparison_headers))
    if chart_times and chart_series:
        chart = workbook.add_chart({"type": "line"})
        for column in range(1, len(comparison_headers)):
            chart.add_series({"name": ["Comparison", 4, column], "categories": ["Comparison", 5, 0, 4 + len(chart_times), 0], "values": ["Comparison", 5, column, 4 + len(chart_times), column]})
        chart.set_title({"name": "Hottest V/I Temperature by Session"}); chart.set_x_axis({"name": "Injection Time (s)"}); chart.set_y_axis({"name": "Temperature (°C)"}); chart.set_legend({"position": "bottom"}); comparison.insert_chart("A5", chart, {"x_scale": 1.6, "y_scale": 1.5})
    _title(compiled, formats, "Compiled Injection Readings", "Long-form readings associated with their original sessions.", 4)
    compiled_headers = ["Session", "Session Folder", "Injection Time (s)", "Hottest V/I (°C)", "Hottest Internal (°C)"]
    compiled_rows = [[r.get(k) for k in ("session", "session_name", "injection_time_s", "hottest_control_c", "hottest_internal_c")] for r in readings]
    _write_table(compiled, formats, 4, compiled_headers, compiled_rows, [25, 25, 20, 20, 22])
    workbook.close()
    return output_path

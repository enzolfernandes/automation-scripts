from __future__ import annotations

import csv
import importlib.util
import json
import os
import subprocess
import tempfile
from pathlib import Path

from .configuration import application_root
from .consolidation import build_compilation_payload


NODE_ROOT = Path(r"C:\Users\NT-0967\.cache\codex-runtimes\codex-primary-runtime\dependencies\node")
NODE_EXE = NODE_ROOT / "bin" / "node.exe"
ARTIFACT_ENTRY = NODE_ROOT / "node_modules" / "@oai" / "artifact-tool" / "dist" / "artifact_tool.mjs"


NUMERIC_FIELDS = {
    "relative_s", "frame", "executing", "progress_status", "profile_elapsed_s", "hottest_control_c",
    "hottest_internal_c", "stability_range_c", "stability_slope_c_per_min", "stable", "initial_target_c", "initial_ready", "initial_baseline_c",
    "initial_baseline_range_c", "initial_baseline_slope_c_per_min", "initial_baseline_observation_s",
    "historical_stress_endpoint", "error_latched", "error_code",
    "V1 Board 1", "V2 Board 1", "V1 Board 2", "V2 Board 2", "I1 Board 1", "I2 Board 1", "I1 Board 2",
    "I2 Board 2", "Main Board 1", "Main Board 2",
}


def excel_generator_available() -> bool:
    return (NODE_EXE.exists() and ARTIFACT_ENTRY.exists()) or importlib.util.find_spec("xlsxwriter") is not None


def _read_csv(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle, delimiter=";"))
    for row in rows:
        for key in NUMERIC_FIELDS.intersection(row):
            value = row[key]
            if value == "" or value is None:
                row[key] = None
            else:
                try:
                    row[key] = int(value) if key in {"frame", "executing", "progress_status", "stable", "initial_ready", "historical_stress_endpoint", "error_latched", "error_code"} else float(value)
                except ValueError:
                    pass
    return rows


def create_excel(bundle: dict[str, Path], output_path: Path) -> Path:
    if not excel_generator_available():
        raise RuntimeError("The Excel report generator is not available in this installation. The evidence CSV files were preserved.")
    payload = {
        "metadata": json.loads(bundle["metadata"].read_text(encoding="utf-8")),
        "samples": _read_csv(bundle["samples"]),
        "events": _read_csv(bundle["events"]),
    }
    if not ARTIFACT_ENTRY.exists():
        from .xlsx_report import create_report

        return create_report(payload, output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    preview_dir = output_path.parent / "previews"
    with tempfile.TemporaryDirectory(prefix="amt205-report-") as temporary:
        input_path = Path(temporary) / "report.json"
        input_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        environment = os.environ.copy()
        environment["ARTIFACT_TOOL_ENTRY"] = str(ARTIFACT_ENTRY)
        result = subprocess.run(
            [str(NODE_EXE), str(application_root() / "report_builder.mjs"), str(input_path), str(output_path), str(preview_dir)],
            capture_output=True, text=True, encoding="utf-8", errors="replace", env=environment,
        )
        if result.returncode:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "Failed to generate the Excel report.")
    return output_path


def create_excel_from_session(session_dir: Path, output_path: Path | None = None) -> Path:
    evidence = session_dir / "evidence"
    bundle = {"metadata": evidence / "metadata.json", "samples": evidence / "readings.csv", "events": evidence / "events.csv"}
    missing = [path.name for path in bundle.values() if not path.is_file()]
    if missing:
        raise RuntimeError(f"The selected session is incomplete. Missing: {', '.join(missing)}")
    return create_excel(bundle, output_path or session_dir / "AMT205_Thermal_Test_Report.xlsx")


def create_session_compilation(session_dirs: list[Path], output_path: Path, source_root: Path | None = None) -> tuple[Path, Path]:
    if not excel_generator_available():
        raise RuntimeError("The Excel report generator is not available in this installation.")
    payload = build_compilation_payload(session_dirs, source_root)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    preview_path = output_path.with_name("AMT205_Thermal_Session_Comparison.png")
    if not ARTIFACT_ENTRY.exists():
        from .xlsx_report import create_compilation

        create_compilation(payload, output_path)
        return output_path, preview_path
    with tempfile.TemporaryDirectory(prefix="amt205-compilation-") as temporary:
        input_path = Path(temporary) / "compilation.json"
        input_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        environment = os.environ.copy()
        environment["ARTIFACT_TOOL_ENTRY"] = str(ARTIFACT_ENTRY)
        result = subprocess.run(
            [
                str(NODE_EXE),
                str(application_root() / "compilation_builder.mjs"),
                str(input_path),
                str(output_path),
                str(preview_path),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=environment,
        )
        if result.returncode:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "Failed to generate the session compilation.")
    return output_path, preview_path

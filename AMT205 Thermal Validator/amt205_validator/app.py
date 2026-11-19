from __future__ import annotations

import json
import os
import queue
import threading
import time
import tkinter as tk
from copy import deepcopy
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import __version__
from .capture import (
    LiveCapture,
    LiveStatusWatchdog,
    RawRecorder,
    interface_alias,
    iter_pcap,
    list_interfaces,
    preferred_interface_index,
    routed_interface_alias,
)
from .configuration import (
    application_root,
    load_config,
    save_config,
    session_config,
    session_preferences,
    update_session_preferences,
)
from .consolidation import discover_session_folders, read_session_summary
from .protocol import CURRENT_SENSORS, INTERNAL_SENSORS, TEMPERATURE_FIELDS, VOLTAGE_SENSORS
from .report import create_excel, create_session_compilation, excel_generator_available
from .session import SampleSnapshot, TestSession, default_session_name
from .workflow import WorkflowStep, format_remaining


PROFILE_LABELS = {
    "current_25": "Current — 1×128 A / 60 A / Power Control 25%",
    "current_50": "Current — 1×128 A / 60 A / Power Control 50%",
    "current_75": "Current — 1×128 A / 60 A / Power Control 75%",
    "current_100": "Current — 1×128 A / 60 A / Power Control 100%",
    "voltage_300": "Voltage — 6×300 V",
    "custom": "Free Profile",
}


def should_auto_finalize(config: dict, workflow_code: str) -> bool:
    """Return whether a completed live evidence workflow should close itself."""
    enabled = config.get("workflow", {}).get("auto_finalize_recording", True)
    return bool(enabled and workflow_code in {"COMPLETE", "REPEAT_REQUIRED"})


CRITERION_LABELS = {
    "monitor_only": "Monitor only — no automatic acceptance",
    "historical_time": "Historical time reference",
    "stable_temperature": "Stable temperature limits",
    "reach_stability": "Reach stability without Thermal Error",
}


class CriteriaDialog(tk.Toplevel):
    def __init__(self, parent: "Application", config: dict) -> None:
        super().__init__(parent)
        self.parent = parent
        self.config = json.loads(json.dumps(config))
        self.title("Acceptance Criteria")
        self.geometry("760x700")
        self.minsize(680, 500)
        self.resizable(True, True)
        self.transient(parent)
        self.grab_set()
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        container = ttk.Frame(self)
        container.grid(row=0, column=0, sticky="nsew")
        container.rowconfigure(0, weight=1)
        container.columnconfigure(0, weight=1)
        canvas = tk.Canvas(container, highlightthickness=0, background="#FFFFFF")
        scrollbar = ttk.Scrollbar(container, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        body = ttk.Frame(canvas, padding=16)
        body_window = canvas.create_window((0, 0), window=body, anchor="nw")
        body.bind("<Configure>", lambda _event: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda event: canvas.itemconfigure(body_window, width=event.width))
        canvas.bind("<MouseWheel>", lambda event: canvas.yview_scroll(int(-event.delta / 120), "units"))
        body.columnconfigure(0, weight=1)
        ttk.Label(body, text="Thermal Test Criteria", font=("Segoe UI", 14, "bold")).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 12))
        fields = [
            ("Initial / Recovery Target — Max Power", ("initial_temperature_c", "max"), "°C"),
            ("Initial / Recovery Target — Normal", ("initial_temperature_c", "normal"), "°C"),
            ("Target Tolerance", ("initial_temperature_c", "tolerance"), "°C"),
            ("Initial Stabilization Hold", ("initial_baseline", "minimum_observation_s"), "s"),
            ("Initial Stabilization Maximum Range", ("initial_baseline", "maximum_range_c"), "°C"),
            ("Initial Stabilization Maximum Absolute Trend", ("initial_baseline", "maximum_abs_slope_c_per_min"), "°C/min"),
            ("Initial Stabilization Maximum Packet Gap", ("initial_baseline", "maximum_sample_gap_s"), "s"),
            ("Recovery Hold", ("thermal_recovery", "hold_s"), "s"),
            ("Recovery Maximum Duration", ("thermal_recovery", "maximum_duration_s"), "s"),
            ("Recovery Maximum Packet Gap", ("thermal_recovery", "maximum_sample_gap_s"), "s"),
            ("Approved Stability Window", ("stability", "window_s"), "s"),
            ("Approved Maximum Range Within Window", ("stability", "maximum_range_c"), "°C"),
            ("Approved Maximum Absolute Stability Trend", ("stability", "maximum_abs_slope_c_per_min"), "°C/min"),
            ("Historical Reference — Current 50%", ("profiles", "current_50", "historical_time_s"), "s"),
            ("Recommended Duration — Current 50%", ("profiles", "current_50", "recommended_duration_s"), "s"),
            ("Historical Reference — Current 75%", ("profiles", "current_75", "historical_time_s"), "s"),
            ("Recommended Duration — Current 75%", ("profiles", "current_75", "recommended_duration_s"), "s"),
            ("Historical Reference — Current 100%", ("profiles", "current_100", "historical_time_s"), "s"),
            ("Recommended Duration — Current 100%", ("profiles", "current_100", "recommended_duration_s"), "s"),
            ("Fan Max Power — Historical Time Multiplier", ("fan_mode", "max_power_historical_time_multiplier"), "×"),
            ("Pass Up To — Current 25%", ("profiles", "current_25", "approved_max_c"), "°C"),
            ("Review Up To — Current 25%", ("profiles", "current_25", "investigate_max_c"), "°C"),
            ("Maximum Duration Without Time Reference", ("profiles", "current_25", "maximum_duration_s"), "s"),
            ("Third Repetition Above Difference", ("repetitions", "third_if_difference_percent_above"), "%"),
            ("Fallback Post-test Evidence", ("workflow", "post_test_evidence_s"), "s"),
        ]
        self.variables: dict[tuple[str, ...], tk.StringVar] = {}
        for row, (label, keys, unit) in enumerate(fields, start=1):
            value = self.config
            for key in keys:
                value = value[key]
            variable = tk.StringVar(value="" if value is None else str(value))
            self.variables[keys] = variable
            ttk.Label(body, text=label).grid(row=row, column=0, sticky="w", pady=3)
            entry = ttk.Entry(body, textvariable=variable, width=12)
            entry.grid(row=row, column=1, padx=8)
            if keys == ("stability", "window_s"):
                self.stability_window_entry = entry
            if keys and keys[0] == "initial_baseline":
                self.baseline_entries = getattr(self, "baseline_entries", [])
                self.baseline_entries.append(entry)
            ttk.Label(body, text=unit).grid(row=row, column=2, sticky="w")
        rules_row = len(fields) + 1
        self.window_approved_var = tk.BooleanVar(value=self.config["stability"].get("window_approved", False))
        self.baseline_enabled_var = tk.BooleanVar(value=self.config.get("initial_baseline", {}).get("enabled", True))
        self.recovery_enabled_var = tk.BooleanVar(value=self.config.get("thermal_recovery", {}).get("enabled", True))
        self.thermal_stop_var = tk.BooleanVar(value=self.config["event_rules"]["thermal_error_always_stops_timer"])
        self.cooling_stop_var = tk.BooleanVar(value=self.config["event_rules"]["cooling_stops_timer_if_output_reduced"])
        self.smart_delay_stop_var = tk.BooleanVar(value=self.config["event_rules"]["smart_delay_stops_timer_if_output_reduced"])
        self.auto_finalize_var = tk.BooleanVar(value=self.config.get("workflow", {}).get("auto_finalize_recording", True))
        self.historical_endpoint_var = tk.BooleanVar(value=self.config["event_rules"].get("status_14_is_historical_stress_endpoint", True))
        ttk.Checkbutton(body, text="Automatically determine the initial operational baseline", variable=self.baseline_enabled_var, command=self._toggle_initial_baseline).grid(row=rules_row, column=0, columnspan=3, sticky="w", pady=(8, 0))
        ttk.Checkbutton(body, text="Record thermal recovery before automatic finalization", variable=self.recovery_enabled_var).grid(row=rules_row + 1, column=0, columnspan=3, sticky="w")
        ttk.Checkbutton(body, text="Use the Stability Window above as an approved acceptance rule", variable=self.window_approved_var, command=self._toggle_stability_window).grid(row=rules_row + 2, column=0, columnspan=3, sticky="w")
        ttk.Checkbutton(body, text="Thermal Error ends the timer", variable=self.thermal_stop_var).grid(row=rules_row + 3, column=0, columnspan=3, sticky="w")
        ttk.Checkbutton(body, text="Cooling ends the timer if output is reduced or paused", variable=self.cooling_stop_var).grid(row=rules_row + 4, column=0, columnspan=3, sticky="w")
        ttk.Checkbutton(body, text="Smart Delay ends the timer if output is reduced or paused", variable=self.smart_delay_stop_var).grid(row=rules_row + 5, column=0, columnspan=3, sticky="w")
        ttk.Checkbutton(body, text="Automatically finish recording after recovery", variable=self.auto_finalize_var).grid(row=rules_row + 6, column=0, columnspan=3, sticky="w")
        ttk.Checkbutton(body, text="Classify progress status 14 as the IA1 Other electrical endpoint", variable=self.historical_endpoint_var).grid(row=rules_row + 7, column=0, columnspan=3, sticky="w")
        ttk.Label(body, text="Initial stabilization and recovery use 25 °C in Max Power and 33 °C in Normal mode. Recovery is operational evidence and does not change the injection acceptance result. IA1 Other is reported separately as an electrical-output occurrence.", foreground="#5C6B73", wraplength=600).grid(row=rules_row + 8, column=0, columnspan=3, sticky="w", pady=(12, 8))
        buttons = ttk.Frame(body)
        buttons.grid(row=rules_row + 9, column=0, columnspan=3, sticky="e")
        ttk.Button(buttons, text="Cancel", command=self.destroy).pack(side="left", padx=4)
        ttk.Button(buttons, text="Save", command=self._save).pack(side="left", padx=4)
        self._toggle_stability_window()
        self._toggle_initial_baseline()

    def _toggle_stability_window(self) -> None:
        self.stability_window_entry.configure(state="normal" if self.window_approved_var.get() else "disabled")

    def _toggle_initial_baseline(self) -> None:
        state = "normal" if self.baseline_enabled_var.get() else "disabled"
        for entry in self.baseline_entries:
            entry.configure(state=state)

    def _save(self) -> None:
        try:
            for keys, variable in self.variables.items():
                if keys == ("stability", "window_s"):
                    continue
                target = self.config
                for key in keys[:-1]:
                    target = target[key]
                target[keys[-1]] = float(variable.get().replace(",", "."))
            if self.window_approved_var.get():
                window_s = float(self.variables[("stability", "window_s")].get().replace(",", "."))
                if window_s <= 0:
                    raise ValueError
                self.config["stability"]["window_s"] = window_s
            else:
                self.config["stability"]["window_s"] = None
            self.config["stability"]["window_approved"] = self.window_approved_var.get()
            self.config["initial_baseline"]["enabled"] = self.baseline_enabled_var.get()
            self.config["initial_baseline"]["use_fan_mode_target"] = True
            self.config["thermal_recovery"]["enabled"] = self.recovery_enabled_var.get()
            self.config["thermal_recovery"]["use_initial_targets"] = True
            if any(value <= 0 for value in (
                self.config["initial_temperature_c"]["max"],
                self.config["initial_temperature_c"]["normal"],
                self.config["initial_baseline"]["minimum_observation_s"],
                self.config["initial_baseline"]["maximum_range_c"],
                self.config["initial_baseline"]["maximum_abs_slope_c_per_min"],
                self.config["initial_baseline"]["maximum_sample_gap_s"],
                self.config["initial_temperature_c"]["tolerance"],
                self.config["thermal_recovery"]["hold_s"],
                self.config["thermal_recovery"]["maximum_duration_s"],
                self.config["thermal_recovery"]["maximum_sample_gap_s"],
                self.config["stability"]["maximum_range_c"],
                self.config["stability"]["maximum_abs_slope_c_per_min"],
                self.config["fan_mode"]["max_power_historical_time_multiplier"],
                self.config["workflow"]["post_test_evidence_s"],
            )):
                raise ValueError
            self.config["profiles"]["voltage_300"]["maximum_duration_s"] = self.config["profiles"]["current_25"]["maximum_duration_s"]
            self.config["event_rules"]["thermal_error_always_stops_timer"] = self.thermal_stop_var.get()
            self.config["event_rules"]["cooling_stops_timer_if_output_reduced"] = self.cooling_stop_var.get()
            self.config["event_rules"]["smart_delay_stops_timer_if_output_reduced"] = self.smart_delay_stop_var.get()
            self.config["event_rules"]["status_14_is_historical_stress_endpoint"] = self.historical_endpoint_var.get()
            self.config.setdefault("workflow", {})["auto_finalize_recording"] = self.auto_finalize_var.get()
        except ValueError:
            messagebox.showerror("Invalid Value", "Use positive numeric values for times, temperatures and ranges.", parent=self)
            return
        save_config(self.config, application_root() / "config" / "user.json")
        self.parent.config_data = self.config
        self.parent.status_var.set("Acceptance criteria updated. The next session will use the new values.")
        self.destroy()


class FreeProfileDialog(tk.Toplevel):
    def __init__(self, parent: "Application", profile: dict) -> None:
        super().__init__(parent)
        self.parent = parent
        self.profile = deepcopy(profile)
        self.title("Free Profile")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()
        body = ttk.Frame(self, padding=16)
        body.grid(sticky="nsew")
        ttk.Label(body, text="Free Profile", font=("Segoe UI", 14, "bold")).grid(row=0, column=0, columnspan=3, sticky="w")
        ttk.Label(
            body,
            text="Use this profile for exploratory or non-standard injections. Choose Monitor only when no approved acceptance rule exists.",
            foreground="#5C6B73",
            wraplength=580,
        ).grid(row=1, column=0, columnspan=3, sticky="w", pady=(4, 12))
        self.name_var = tk.StringVar(value=self.profile.get("label", "Free Profile"))
        self.signal_var = tk.StringVar(value=self.profile.get("signal_type", "Other"))
        criterion = self.profile.get("criterion", "monitor_only")
        self.criterion_var = tk.StringVar(value=CRITERION_LABELS.get(criterion, CRITERION_LABELS["monitor_only"]))
        self.reference_var = tk.StringVar(value="" if self.profile.get("historical_time_s") is None else str(self.profile["historical_time_s"]))
        self.pass_var = tk.StringVar(value="" if self.profile.get("approved_max_c") is None else str(self.profile["approved_max_c"]))
        self.review_var = tk.StringVar(value="" if self.profile.get("investigate_max_c") is None else str(self.profile["investigate_max_c"]))
        self.duration_var = tk.StringVar(value=str(self.profile.get("maximum_duration_s", 1800.0)))
        specs = [
            ("Profile Name", ttk.Entry(body, textvariable=self.name_var, width=42), ""),
            ("Signal Type", ttk.Combobox(body, textvariable=self.signal_var, values=("Current", "Voltage", "Mixed", "Other"), state="readonly", width=39), ""),
            ("Acceptance Criterion", ttk.Combobox(body, textvariable=self.criterion_var, values=tuple(CRITERION_LABELS.values()), state="readonly", width=39), ""),
            ("Historical Time Reference", ttk.Entry(body, textvariable=self.reference_var, width=14), "s"),
            ("Pass Up To", ttk.Entry(body, textvariable=self.pass_var, width=14), "°C"),
            ("Review Up To", ttk.Entry(body, textvariable=self.review_var, width=14), "°C"),
            ("Maximum Duration", ttk.Entry(body, textvariable=self.duration_var, width=14), "s"),
        ]
        for row, (label, widget, unit) in enumerate(specs, start=2):
            ttk.Label(body, text=label).grid(row=row, column=0, sticky="w", pady=4)
            widget.grid(row=row, column=1, sticky="w", padx=8)
            ttk.Label(body, text=unit).grid(row=row, column=2, sticky="w")
        buttons = ttk.Frame(body)
        buttons.grid(row=10, column=0, columnspan=3, sticky="e", pady=(14, 0))
        ttk.Button(buttons, text="Cancel", command=self.destroy).pack(side="left", padx=4)
        ttk.Button(buttons, text="Apply", command=self._apply).pack(side="left", padx=4)

    @staticmethod
    def _optional_float(value: str) -> float | None:
        stripped = value.strip()
        return None if not stripped else float(stripped.replace(",", "."))

    def _apply(self) -> None:
        criterion = next(key for key, label in CRITERION_LABELS.items() if label == self.criterion_var.get())
        try:
            profile = {
                "label": self.name_var.get().strip() or "Free Profile",
                "signal_type": self.signal_var.get(),
                "criterion": criterion,
                "historical_time_s": self._optional_float(self.reference_var.get()),
                "approved_max_c": self._optional_float(self.pass_var.get()),
                "investigate_max_c": self._optional_float(self.review_var.get()),
                "maximum_duration_s": float(self.duration_var.get().replace(",", ".")),
            }
        except ValueError:
            messagebox.showerror("Invalid Value", "Use numeric values in the reference and limit fields.", parent=self)
            return
        if criterion == "historical_time" and profile["historical_time_s"] is None:
            messagebox.showerror("Missing Reference", "Historical Time Reference is required for this criterion.", parent=self)
            return
        if criterion == "stable_temperature" and (profile["approved_max_c"] is None or profile["investigate_max_c"] is None):
            messagebox.showerror("Missing Limits", "Pass Up To and Review Up To are required for this criterion.", parent=self)
            return
        self.parent.custom_profile = profile
        self.parent.config_data["profiles"]["custom"] = deepcopy(profile)
        save_config(self.parent.config_data, application_root() / "config" / "user.json")
        self.parent.status_var.set(f"Free Profile configured: {profile['label']}.")
        self.parent._update_setup_summary()
        self.destroy()


class Application(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(f"AMT205 Thermal Validator {__version__}")
        self.geometry("1220x830")
        self.minsize(1020, 740)
        self.config_data = load_config()
        self.custom_profile = deepcopy(self.config_data["profiles"]["custom"])
        self.messages: queue.Queue[tuple[str, object]] = queue.Queue()
        self.session: TestSession | None = None
        self.recording_session: TestSession | None = None
        self.live_capture: LiveCapture | None = None
        self.raw_recorder: RawRecorder | None = None
        self.recording_origin_relative: float | None = None
        self.live_interface_number: str | None = None
        self.report_busy = False
        self.compilation_busy = False
        self.auto_finalize_pending = False
        self._preferences_after_id: str | None = None
        self.current_source: Path | None = None
        self.current_output: Path | None = None
        self.current_report: Path | None = None
        self.compilation_sessions: list[Path] = []
        self.compilation_output: Path | None = None
        self.compilation_report: Path | None = None
        self.compilation_preview: Path | None = None
        self.interfaces: list[tuple[str, str]] = []
        self.live_watchdog = LiveStatusWatchdog(timeout_s=5.0)
        self._build_style()
        self._build_ui()
        self.bind("<F5>", lambda _event: self.toggle_capture())
        self.bind("<F6>", lambda _event: self.toggle_recording())
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.after(100, self._poll_messages)
        self.after(200, self._load_interfaces)

    def _build_style(self) -> None:
        style = ttk.Style(self)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        style.configure("Title.TLabel", font=("Segoe UI", 18, "bold"), foreground="#17324D")
        style.configure("CardValue.TLabel", font=("Segoe UI", 17, "bold"), foreground="#17324D")
        style.configure("CardValueSmall.TLabel", font=("Segoe UI", 14, "bold"), foreground="#17324D")
        style.configure("CardLabel.TLabel", font=("Segoe UI", 9), foreground="#5C6B73")
        style.configure("Voltage.CardLabel.TLabel", font=("Segoe UI", 9, "bold"), foreground="#1F6FEB")
        style.configure("Current.CardLabel.TLabel", font=("Segoe UI", 9, "bold"), foreground="#B45309")
        style.configure("Section.TLabel", font=("Segoe UI", 11, "bold"), foreground="#17324D")
        style.configure("Hint.TLabel", font=("Segoe UI", 9), foreground="#5C6B73")
        style.configure("Primary.TButton", font=("Segoe UI", 10, "bold"))
        style.configure("NextStepTitle.TLabel", font=("Segoe UI", 13, "bold"), foreground="#17324D")
        style.configure("NextStepInstruction.TLabel", font=("Segoe UI", 11), foreground="#17324D")
        style.configure("NextStepTime.TLabel", font=("Segoe UI", 16, "bold"), foreground="#1B7F79")

    def _build_ui(self) -> None:
        preferences = session_preferences(self.config_data)
        root = ttk.Frame(self, padding=14)
        root.pack(fill="both", expand=True)
        header = ttk.Frame(root)
        header.pack(fill="x")
        ttk.Label(header, text="AMT205 Thermal Validation", style="Title.TLabel").pack(side="left")
        ttk.Label(header, text=f"v{__version__} · passive monitor", foreground="#5C6B73").pack(side="left", padx=12, pady=(8, 0))

        self.notebook = ttk.Notebook(root)
        self.notebook.pack(fill="both", expand=True, pady=(10, 0))
        monitor_tab = ttk.Frame(self.notebook, padding=10)
        self.settings_tab = ttk.Frame(self.notebook, padding=12)
        self.compilation_tab = ttk.Frame(self.notebook, padding=12)
        guide_tab = ttk.Frame(self.notebook, padding=12)
        self.notebook.add(monitor_tab, text="Monitor")
        self.notebook.add(self.settings_tab, text="Settings")
        self.notebook.add(self.compilation_tab, text="Session Compilation")
        self.notebook.add(guide_tab, text="Guide")
        self.notebook.bind("<<NotebookTabChanged>>", self._notebook_tab_changed)

        # Monitor: only the controls and information needed during a test.
        connection = ttk.LabelFrame(monitor_tab, text="Connection", padding=10)
        connection.pack(fill="x", pady=(0, 8))
        self.source_mode = tk.StringVar(value=str(preferences["source_mode"]))
        ttk.Label(connection, text="Source").grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.source_combo = ttk.Combobox(connection, textvariable=self.source_mode, values=("PCAP File", "Live Monitor"), state="readonly", width=16)
        self.source_combo.grid(row=0, column=1, padx=(0, 8), sticky="w")
        self.source_combo.bind("<<ComboboxSelected>>", lambda _event: self._source_changed())
        self.source_value = tk.StringVar(value="Select a .pcapng capture")
        self.source_entry = ttk.Entry(connection, textvariable=self.source_value, width=48, state="readonly")
        self.source_entry.grid(row=0, column=2, padx=4, sticky="ew")
        self.browse_button = ttk.Button(connection, text="Select File", command=self._choose_pcap)
        self.browse_button.grid(row=0, column=3, padx=4)
        self.interface_combo = ttk.Combobox(connection, state="readonly", width=42)
        self.interface_combo.grid(row=0, column=2, padx=4, sticky="ew")
        self.interface_combo.grid_remove()
        self.refresh_button = ttk.Button(connection, text="Refresh", command=self._load_interfaces)
        self.refresh_button.grid(row=0, column=3, padx=4)
        self.refresh_button.grid_remove()
        self.start_button = ttk.Button(connection, text="Analyze / F5", style="Primary.TButton", command=self.toggle_capture)
        self.start_button.grid(row=0, column=4, padx=(12, 0))
        self.record_button = ttk.Button(connection, text="Start Recording / F6", command=self.toggle_recording, state="disabled")
        self.record_button.grid(row=0, column=5, padx=(6, 0))
        self.record_button.grid_remove()
        connection.columnconfigure(2, weight=1)
        self.setup_summary_var = tk.StringVar()
        self.observed_fan_var = tk.StringVar(value="Fan Mode observed: not identified")
        ttk.Label(connection, textvariable=self.setup_summary_var, style="Hint.TLabel").grid(row=1, column=0, columnspan=4, sticky="w", pady=(8, 0))
        ttk.Label(connection, textvariable=self.observed_fan_var, style="Hint.TLabel").grid(row=1, column=4, columnspan=2, sticky="e", pady=(8, 0))

        next_step = ttk.LabelFrame(monitor_tab, text="Next Step", padding=(12, 8))
        next_step.pack(fill="x", pady=(0, 6))
        next_step.columnconfigure(0, weight=1)
        self.next_step_title_var = tk.StringVar(value="Select a source")
        self.next_step_instruction_var = tk.StringVar(value="Connect to the AMT205 or select a PCAP file to begin.")
        self.next_step_detail_var = tk.StringVar(value="Guidance is passive and follows the decoded packet states.")
        self.next_step_time_var = tk.StringVar(value="")
        ttk.Label(next_step, textvariable=self.next_step_title_var, style="NextStepTitle.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(next_step, textvariable=self.next_step_instruction_var, style="NextStepInstruction.TLabel", wraplength=900, justify="left").grid(row=1, column=0, sticky="w", pady=(2, 0))
        ttk.Label(next_step, textvariable=self.next_step_detail_var, style="Hint.TLabel", wraplength=900, justify="left").grid(row=2, column=0, sticky="w", pady=(3, 0))
        ttk.Label(next_step, textvariable=self.next_step_time_var, style="NextStepTime.TLabel", anchor="e", justify="right").grid(row=0, column=1, rowspan=3, sticky="e", padx=(18, 0))

        cards = ttk.Frame(monitor_tab)
        cards.pack(fill="x", pady=6)
        self.card_vars = {
            "state": tk.StringVar(value="WAITING"),
            "elapsed": tk.StringVar(value="—"),
            "voltage": tk.StringVar(value="—"),
            "current": tk.StringVar(value="—"),
            "internal": tk.StringVar(value="—"),
            "result": tk.StringVar(value="Overall: INCONCLUSIVE\nThermal: INCONCLUSIVE\nElectrical: NOT ASSESSED"),
        }
        labels = [
            ("state", "State", "CardLabel.TLabel"),
            ("elapsed", "State Time", "CardLabel.TLabel"),
            ("voltage", "Hottest Voltage", "Voltage.CardLabel.TLabel"),
            ("current", "Hottest Current", "Current.CardLabel.TLabel"),
            ("internal", "Hottest Other (CPU excluded)", "CardLabel.TLabel"),
            ("result", "Results", "CardLabel.TLabel"),
        ]
        for index, (key, label, label_style) in enumerate(labels):
            card = ttk.LabelFrame(cards, padding=8)
            card.grid(row=0, column=index, padx=3, sticky="nsew")
            ttk.Label(card, text=label, style=label_style).pack(anchor="w")
            value_style = "CardValueSmall.TLabel" if key in ("state", "result") else "CardValue.TLabel"
            wraplength = 220 if key == "internal" else 175
            ttk.Label(card, textvariable=self.card_vars[key], style=value_style, wraplength=wraplength).pack(anchor="w", pady=(4, 0))
            cards.columnconfigure(index, weight=1)

        panes = ttk.Panedwindow(monitor_tab, orient="horizontal")
        panes.pack(fill="both", expand=True, pady=6)
        sensor_frame = ttk.LabelFrame(panes, text="Temperature by channel type", padding=8)
        event_frame = ttk.LabelFrame(panes, text="Session Events", padding=8)
        panes.add(sensor_frame, weight=3)
        panes.add(event_frame, weight=2)
        sensor_table = ttk.Frame(sensor_frame)
        sensor_table.pack(fill="both", expand=True)
        self.sensor_tree = ttk.Treeview(sensor_table, columns=("sensor", "signal", "temperature", "role"), show="headings", height=10)
        self.sensor_tree.heading("sensor", text="Sensor")
        self.sensor_tree.heading("signal", text="Channel Type")
        self.sensor_tree.heading("temperature", text="Temperature")
        self.sensor_tree.heading("role", text="Use")
        self.sensor_tree.column("sensor", width=135)
        self.sensor_tree.column("signal", width=95)
        self.sensor_tree.column("temperature", width=100, anchor="e")
        self.sensor_tree.column("role", width=145)
        sensor_scroll = ttk.Scrollbar(sensor_table, orient="vertical", command=self.sensor_tree.yview)
        self.sensor_tree.configure(yscrollcommand=sensor_scroll.set)
        self.sensor_tree.pack(side="left", fill="both", expand=True)
        sensor_scroll.pack(side="right", fill="y")
        self.sensor_tree.tag_configure("voltage", background="#EAF3FB", foreground="#174A7E")
        self.sensor_tree.tag_configure("current", background="#FFF2E2", foreground="#8A4B08")
        self.sensor_tree.tag_configure("internal", background="#F3F5F7", foreground="#374151")
        self.sensor_tree.tag_configure("excluded", background="#ECEFF1", foreground="#6B7280")
        for name, _ in TEMPERATURE_FIELDS:
            if name in VOLTAGE_SENSORS:
                signal, tag, role = "Voltage", "voltage", "Acceptance control"
            elif name in CURRENT_SENSORS:
                signal, tag, role = "Current", "current", "Acceptance control"
            else:
                signal, tag, role = "Internal", "internal", "Informative"
            self.sensor_tree.insert("", "end", iid=name, values=(name, signal, "—", role), tags=(tag,))
        self.sensor_tree.insert("", "end", iid="CPU", values=("CPU Temperature", "CPU", "Not mapped", "Excluded"), tags=("excluded",))

        self.event_tree = ttk.Treeview(event_frame, columns=("time", "severity", "message"), show="headings", height=10)
        self.event_tree.heading("time", text="Time")
        self.event_tree.heading("severity", text="Level")
        self.event_tree.heading("message", text="Event")
        self.event_tree.column("time", width=85)
        self.event_tree.column("severity", width=75)
        self.event_tree.column("message", width=390)
        self.event_tree.pack(fill="both", expand=True)

        footer = ttk.Frame(monitor_tab)
        footer.pack(fill="x", pady=(6, 0))
        footer.columnconfigure(0, weight=1)
        self.status_var = tk.StringVar(value="Ready. Select a PCAP file or connect the Live Monitor to view current temperatures.")
        ttk.Label(footer, textvariable=self.status_var, foreground="#5C6B73", width=1, anchor="w").grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self.open_folder_button = ttk.Button(footer, text="Open Output Folder", width=18, command=self._open_folder, state="disabled")
        self.open_folder_button.grid(row=0, column=1, padx=4)
        self.open_report_button = ttk.Button(footer, text="Open Report", width=12, command=self._open_report, state="disabled")
        self.open_report_button.grid(row=0, column=2, padx=(4, 0))

        # Session Compilation: combines completed evidence bundles without reopening PCAP files.
        ttk.Label(
            self.compilation_tab,
            text="Select the folder that represents one successive test series. The validator orders its completed sessions as thermal cycles and creates one Excel workbook plus one comparison graph, including intervals and recovery results.",
            style="Hint.TLabel",
            wraplength=1120,
            justify="left",
        ).pack(fill="x", pady=(0, 10))
        compilation_source = ttk.LabelFrame(self.compilation_tab, text="Sessions Folder", padding=10)
        compilation_source.pack(fill="x", pady=(0, 10))
        compilation_source.columnconfigure(0, weight=1)
        compilation_default = str(preferences["output_location"]).strip() or str(application_root() / "sessions")
        self.compilation_root_var = tk.StringVar(value=compilation_default)
        ttk.Entry(compilation_source, textvariable=self.compilation_root_var, state="readonly").grid(row=0, column=0, sticky="ew", padx=(0, 8))
        ttk.Button(compilation_source, text="Select Folder…", command=self._choose_compilation_folder).grid(row=0, column=1)

        compilation_list = ttk.LabelFrame(self.compilation_tab, text="Detected Sessions", padding=8)
        compilation_list.pack(fill="both", expand=True)
        tree_container = ttk.Frame(compilation_list)
        tree_container.pack(fill="both", expand=True)
        self.compilation_tree = ttk.Treeview(
            tree_container,
            columns=("cycle", "session", "date", "serial", "profile", "repetition", "fan", "recovery", "quantitative", "recorded"),
            show="headings",
            height=13,
        )
        for key, label, width in (
            ("cycle", "Cycle", 55),
            ("session", "Session", 190),
            ("date", "Date / Time", 155),
            ("serial", "Serial Number", 120),
            ("profile", "Profile", 145),
            ("repetition", "Rep.", 55),
            ("fan", "Fan Mode", 140),
            ("recovery", "Recovery", 105),
            ("quantitative", "Quantitative", 115),
            ("recorded", "Recorded Result", 125),
        ):
            self.compilation_tree.heading(key, text=label)
            self.compilation_tree.column(key, width=width, anchor="center" if key in {"cycle", "repetition", "fan", "recovery", "quantitative", "recorded"} else "w")
        compilation_scroll = ttk.Scrollbar(tree_container, orient="vertical", command=self.compilation_tree.yview)
        self.compilation_tree.configure(yscrollcommand=compilation_scroll.set)
        self.compilation_tree.pack(side="left", fill="both", expand=True)
        compilation_scroll.pack(side="right", fill="y")

        compilation_footer = ttk.Frame(self.compilation_tab)
        compilation_footer.pack(fill="x", pady=(10, 0))
        compilation_footer.columnconfigure(0, weight=1)
        self.compilation_status_var = tk.StringVar(value="Select a sessions folder to begin.")
        ttk.Label(compilation_footer, textvariable=self.compilation_status_var, style="Hint.TLabel").grid(row=0, column=0, sticky="w")
        self.compilation_generate_button = ttk.Button(compilation_footer, text="Generate Compilation", command=self._generate_compilation, state="disabled")
        self.compilation_generate_button.grid(row=0, column=1, padx=4)
        self.compilation_open_folder_button = ttk.Button(compilation_footer, text="Open Output Folder", command=self._open_compilation_folder, state="disabled")
        self.compilation_open_folder_button.grid(row=0, column=2, padx=4)
        self.compilation_open_report_button = ttk.Button(compilation_footer, text="Open Compilation", command=self._open_compilation_report, state="disabled")
        self.compilation_open_report_button.grid(row=0, column=3, padx=(4, 0))

        # Settings: values recorded in the session and copied into the report.
        self.profile_var = tk.StringVar(value=PROFILE_LABELS.get(str(preferences["profile"]), PROFILE_LABELS["current_50"]))
        self.serial_var = tk.StringVar(value=str(preferences["serial_number"]))
        self.ambient_var = tk.StringVar(value=str(preferences["ambient_c"]))
        self.repetition_var = tk.StringVar(value=str(preferences["repetition"]))
        output_location = str(preferences["output_location"]).strip() or str(application_root() / "sessions")
        self.output_base_var = tk.StringVar(value=output_location)
        self.smart_delay_var = tk.BooleanVar(value=bool(preferences["smart_delay_enabled"]))
        self.smart_min_var = tk.StringVar(value=str(preferences["smart_delay_min_c"]))
        self.smart_max_var = tk.StringVar(value=str(preferences["smart_delay_max_c"]))
        self.max_value_var = tk.StringVar(value=str(preferences["maximum_temperature_setting_c"]))
        self.earth_enable_var = tk.BooleanVar(value=bool(preferences["earth_enabled"]))
        self.max_power_var = tk.BooleanVar(value=bool(preferences["fan_mode_max_power"]))
        self.feedback_var = tk.BooleanVar(value=bool(preferences["compare_feedback"]))

        ttk.Label(
            self.settings_tab,
            text="These values document the test and are copied into the report. They do not change AMPro or transmit commands to the test set.",
            style="Hint.TLabel",
            wraplength=1120,
        ).pack(fill="x", pady=(0, 10))
        settings_book = ttk.Notebook(self.settings_tab)
        settings_book.pack(fill="both", expand=True)
        session_settings = ttk.Frame(settings_book, padding=12)
        ampro_settings = ttk.Frame(settings_book, padding=12)
        settings_book.add(session_settings, text="Session and Report")
        settings_book.add(ampro_settings, text="AMPro and Criteria")
        for frame in (session_settings, ampro_settings):
            frame.columnconfigure(1, weight=1)
            frame.columnconfigure(2, weight=2)
            ttk.Label(frame, text="Item", style="Section.TLabel").grid(row=0, column=0, sticky="w", pady=(0, 8))
            ttk.Label(frame, text="Value", style="Section.TLabel").grid(row=0, column=1, sticky="w", pady=(0, 8))
            ttk.Label(frame, text="What it means", style="Section.TLabel").grid(row=0, column=2, sticky="w", pady=(0, 8))

        def setting_row(frame, row: int, label: str, control: tk.Widget, explanation: str) -> None:
            ttk.Label(frame, text=label).grid(row=row, column=0, sticky="nw", padx=(0, 8), pady=4)
            control.grid(row=row, column=1, sticky="nw", padx=(0, 10), pady=2)
            ttk.Label(frame, text=explanation, style="Hint.TLabel", wraplength=500, justify="left").grid(row=row, column=2, sticky="nw", pady=3)

        output_controls = ttk.Frame(session_settings)
        ttk.Entry(output_controls, textvariable=self.output_base_var, state="readonly", width=42).pack(side="left")
        ttk.Button(output_controls, text="Select…", command=self._choose_output_folder).pack(side="left", padx=4)
        setting_row(session_settings, 1, "Output Location", output_controls, "Folder where each session, its Excel report and technical evidence will be saved.")

        profile_controls = ttk.Frame(session_settings)
        profile_combo = ttk.Combobox(profile_controls, textvariable=self.profile_var, values=tuple(PROFILE_LABELS.values()), state="readonly", width=45)
        profile_combo.pack(anchor="w")
        profile_combo.bind("<<ComboboxSelected>>", lambda _event: self._profile_changed())
        self.free_profile_button = ttk.Button(profile_controls, text="Configure Free Profile", command=lambda: FreeProfileDialog(self, self.custom_profile))
        self.free_profile_button.pack(anchor="w", pady=(4, 0))
        self.free_profile_button.pack_forget()
        setting_row(session_settings, 2, "Profile", profile_controls, "Selects the approved acceptance rule. Free Profile is intended for exploratory or non-standard injections.")
        setting_row(session_settings, 3, "Serial Number", ttk.Entry(session_settings, textvariable=self.serial_var, width=23), "Identifies the physical AMT205 unit in the report and traceability records.")
        setting_row(session_settings, 4, "Ambient Temperature", ttk.Entry(session_settings, textvariable=self.ambient_var, width=10), "Room temperature measured near the test set before the test, in °C.")
        setting_row(session_settings, 5, "Repetition", ttk.Combobox(session_settings, textvariable=self.repetition_var, values=("1", "2", "3"), state="readonly", width=8), "Run number for the same test point. Two repetitions are normally required; a third may be requested.")

        setting_row(ampro_settings, 1, "Smart Delay (Turn On/Off Fan)", ttk.Checkbutton(ampro_settings, variable=self.smart_delay_var), "Records whether AMPro automatically turns the fan on or off between the Min. and Max. thresholds.")
        setting_row(ampro_settings, 2, "Min.", ttk.Entry(ampro_settings, textvariable=self.smart_min_var, width=10), "Lower Smart Delay temperature threshold, in °C.")
        setting_row(ampro_settings, 3, "Max.", ttk.Entry(ampro_settings, textvariable=self.smart_max_var, width=10), "Upper Smart Delay temperature threshold, in °C.")
        setting_row(ampro_settings, 4, "Max. Value", ttk.Entry(ampro_settings, textvariable=self.max_value_var, width=10), "Maximum temperature configured in AMPro. It is recorded for traceability and is not read from the protocol.")
        setting_row(ampro_settings, 5, "Earth — Enable", ttk.Checkbutton(ampro_settings, variable=self.earth_enable_var), "Records that AMPro earth protection is enabled.")
        setting_row(ampro_settings, 6, "Fan Mode (Max Power)", ttk.Checkbutton(ampro_settings, variable=self.max_power_var), "Enable for Max Power. Clear for Fan Mode Normal. Historical time acceptance applies only to Max Power; Normal sessions are comparative.")
        setting_row(ampro_settings, 7, "Setpoint vs. Internal Feedback", ttk.Checkbutton(ampro_settings, variable=self.feedback_var), "Recommended optional comparison when both commanded and internally measured electrical values are available.")
        criteria_controls = ttk.Frame(ampro_settings)
        ttk.Button(criteria_controls, text="Edit Acceptance Criteria…", command=lambda: CriteriaDialog(self, self.config_data)).pack(anchor="w")
        setting_row(ampro_settings, 8, "Acceptance Criteria", criteria_controls, "Opens automatic initial-baseline, injection-stability, historical-time, thermal-limit and repetition rules. Changes apply to the next session.")

        # Guide: in-application operating instructions and field glossary.
        guide_frame = ttk.Frame(guide_tab)
        guide_frame.pack(fill="both", expand=True)
        guide_scroll = ttk.Scrollbar(guide_frame, orient="vertical")
        guide_text = tk.Text(
            guide_frame,
            wrap="word",
            relief="flat",
            background="#FFFFFF",
            foreground="#263238",
            font=("Segoe UI", 10),
            padx=18,
            pady=14,
            yscrollcommand=guide_scroll.set,
        )
        guide_scroll.configure(command=guide_text.yview)
        guide_text.pack(side="left", fill="both", expand=True)
        guide_scroll.pack(side="right", fill="y")
        guide_text.tag_configure("title", font=("Segoe UI", 16, "bold"), foreground="#17324D", spacing3=8)
        guide_text.tag_configure("heading", font=("Segoe UI", 11, "bold"), foreground="#17324D", spacing1=12, spacing3=4)
        guide_text.tag_configure("voltage", foreground="#1F6FEB", font=("Segoe UI", 10, "bold"))
        guide_text.tag_configure("current", foreground="#B45309", font=("Segoe UI", 10, "bold"))
        guide_sections = [
            ("title", "AMT205 Thermal Validator — Guide\n"),
            ("heading", "Quick workflow\n"),
            (None, "1. Open Settings and record the session, AMPro and report information.\n2. Select the Fan Mode used in AMPro. Max Power uses the approved historical-time reference; Normal mode is recorded as a comparative route. The historical comparison condition is 50 Hz.\n3. Return to Monitor and choose Live Monitor.\n4. Confirm the Ethernet adapter marked AMT route, then press Connect / F5.\n5. Wait for temperatures and press Start Recording / F6.\n6. Follow Next Step: Max Power stabilizes around 25 °C and Normal around 33 °C before injection.\n7. When instructed, stop injection in AMPro. The validator keeps recording until the applicable recovery target is reached and held, or until the configured recovery limit.\n8. Review the Excel result and technical evidence before signing the test.\n"),
            ("heading", "Temperature display\n"),
            ("voltage", "Voltage — V1/V2 Board sensors. "),
            (None, "These four rows monitor the voltage-channel boards and participate in the acceptance control temperature.\n"),
            ("current", "Current — I1/I2 Board sensors. "),
            (None, "These four rows monitor the current-channel boards and also participate in the acceptance control temperature.\nInternal — Main Board sensors are informative. CPU Temperature remains not mapped and is excluded. The formal criterion uses the hottest value among all Voltage and Current sensors.\n"),
            ("heading", "Monitor items\n"),
            (None, "Source selects a saved PCAP/PCAPNG file or direct network observation. AMT route identifies the Windows adapter used to reach 192.168.1.41. Connect starts continuous temperature monitoring; Start Recording begins the evidence and report window. Next Step verifies the fan-specific initial stabilization while injection is OFF, then follows the decoded injection state and post-injection recovery. Initial stabilization and recovery are operational measurements, not injection-acceptance rules. With automatic finalization enabled, the report is generated after recovery completes or reaches its configured time limit; no command is sent to AMPro or the test set. State reports IDLE or INJECTION ACTIVE. State Time is the elapsed time transmitted by the AMT205 when available. Current Result is recalculated from the selected profile and recorded evidence while recording is active.\n"),
            ("heading", "Results\n"),
            (None, "Thermal Result evaluates the approved temperature or time rule. Electrical Output Result records IA1 Other separately. Overall Result becomes REVIEW when thermal evidence passes or remains incomplete but an electrical-output occurrence was observed. PASS means the configured thermal rule was met without a decoded electrical-output error; FAIL means an approved thermal failure rule was observed.\n"),
            ("heading", "Session compilation\n"),
            (None, "Open Session Compilation and select the folder that represents one successive test series. Sessions are ordered chronologically and numbered as thermal cycles. Generate Compilation creates one Excel workbook with cycle order, interval since the previous session, initial and maximum temperatures, recovery result, a common-time comparison chart and long-form injection readings. A PNG copy of the comparison chart is saved beside the workbook. Existing evidence files are read without being modified.\n"),
            ("heading", "Important safeguards\n"),
            (None, "The application is passive: it never sends commands to AMPro or the AMT205. Progress status 14 is recorded as the operator-confirmed IA1 Other electrical endpoint; it does not complete an unmet thermal rule. Internal code 6 remains Thermal Error. Temperatures can be displayed when connecting in the middle of a test, but a formal profile remains INCONCLUSIVE if the approved initial condition was not observed. If the status stream is silent for five seconds, stale temperatures are cleared and DISCONNECTED is recorded; reconnection is also recorded when packets return. Session settings are saved automatically and restored on the next startup. Settings are copied when a session starts; edits made during a running session apply only to the next session.\n"),
        ]
        for tag, content in guide_sections:
            guide_text.insert("end", content, tag or ())
        guide_text.configure(state="disabled")

        for variable in (self.profile_var, self.ambient_var, self.repetition_var, self.max_power_var):
            variable.trace_add("write", lambda *_args: self._update_setup_summary())
        for variable in (
            self.source_mode, self.profile_var, self.serial_var, self.ambient_var, self.repetition_var,
            self.output_base_var, self.smart_delay_var, self.smart_min_var, self.smart_max_var,
            self.max_value_var, self.earth_enable_var, self.max_power_var, self.feedback_var,
        ):
            variable.trace_add("write", lambda *_args: self._schedule_preferences_save())
        self._update_setup_summary()
        self._source_changed()

    def _preference_values(self) -> dict:
        profile = next((key for key, label in PROFILE_LABELS.items() if label == self.profile_var.get()), "current_50")
        return {
            "source_mode": self.source_mode.get(),
            "profile": profile,
            "serial_number": self.serial_var.get(),
            "ambient_c": self.ambient_var.get(),
            "repetition": self.repetition_var.get(),
            "output_location": self.output_base_var.get(),
            "smart_delay_enabled": self.smart_delay_var.get(),
            "smart_delay_min_c": self.smart_min_var.get(),
            "smart_delay_max_c": self.smart_max_var.get(),
            "maximum_temperature_setting_c": self.max_value_var.get(),
            "earth_enabled": self.earth_enable_var.get(),
            "fan_mode_max_power": self.max_power_var.get(),
            "compare_feedback": self.feedback_var.get(),
        }

    def _schedule_preferences_save(self) -> None:
        if self._preferences_after_id is not None:
            self.after_cancel(self._preferences_after_id)
        self._preferences_after_id = self.after(500, self._save_preferences)

    def _save_preferences(self) -> None:
        self._preferences_after_id = None
        update_session_preferences(self.config_data, self._preference_values())
        save_config(self.config_data, application_root() / "config" / "user.json")

    def _source_changed(self) -> None:
        live = self.source_mode.get() == "Live Monitor"
        if live:
            self.source_entry.grid_remove(); self.browse_button.grid_remove()
            self.interface_combo.grid(); self.refresh_button.grid()
            self.record_button.grid()
            self.record_button.configure(text="Start Recording / F6", state="disabled")
            self.start_button.configure(text="Connect / F5")
            self.status_var.set("Connect starts live monitoring. Start Recording creates the evidence and report session.")
            self._set_next_step(
                title="Connect to the AMT205",
                instruction="Press Connect / F5 and wait for live temperature readings.",
                detail="The minimum test workflow starts only after Start Recording / F6.",
            )
        else:
            self.interface_combo.grid_remove(); self.refresh_button.grid_remove()
            self.record_button.grid_remove()
            self.source_entry.grid(); self.browse_button.grid()
            self.start_button.configure(text="Analyze / F5")
            self._set_next_step(
                title="Select a packet capture",
                instruction="Choose a PCAP/PCAPNG file and press Analyze / F5.",
                detail="Recorded workflow transitions will be reconstructed from the capture.",
            )

    def _profile_changed(self) -> None:
        if self.profile_var.get() == PROFILE_LABELS["custom"]:
            self.free_profile_button.pack(anchor="w", pady=(4, 0))
        else:
            self.free_profile_button.pack_forget()
        self._update_setup_summary()

    def _update_setup_summary(self) -> None:
        if not hasattr(self, "setup_summary_var") or not hasattr(self, "profile_var"):
            return
        profile = self.profile_var.get()
        if profile == PROFILE_LABELS["custom"]:
            profile = self.custom_profile.get("label", "Free Profile")
        fan = "Max Power" if self.max_power_var.get() else "Normal (comparative)"
        ambient = self.ambient_var.get().strip() or "not set"
        repetition = self.repetition_var.get() or "not set"
        self.setup_summary_var.set(f"Settings: {profile} · Fan Mode: {fan} · Ambient: {ambient} °C · Repetition: {repetition}")

    def _set_next_step(self, step: WorkflowStep | None = None, *, title: str = "", instruction: str = "", detail: str = "") -> None:
        if step is not None:
            title = step.title
            instruction = step.instruction
            detail = step.detail
            self.next_step_time_var.set(
                f"MINIMUM REMAINING\n{format_remaining(step.remaining_s)}" if step.remaining_s is not None else ""
            )
        else:
            self.next_step_time_var.set("")
        self.next_step_title_var.set(title)
        self.next_step_instruction_var.set(instruction)
        self.next_step_detail_var.set(detail)

    def _choose_pcap(self) -> None:
        selected = filedialog.askopenfilename(title="Select Capture", filetypes=(("Packet Capture", "*.pcapng *.pcap"), ("All Files", "*.*")))
        if selected:
            self.current_source = Path(selected)
            self.source_value.set(selected)

    def _choose_output_folder(self) -> None:
        selected = filedialog.askdirectory(title="Select Output Location", initialdir=self.output_base_var.get())
        if selected:
            self.output_base_var.set(selected)
            self.status_var.set(f"Output location selected: {selected}")

    def _choose_compilation_folder(self) -> None:
        selected = filedialog.askdirectory(title="Select Sessions Folder", initialdir=self.compilation_root_var.get())
        if selected:
            self.compilation_root_var.set(selected)
            self._refresh_compilation_sessions()

    def _notebook_tab_changed(self, _event=None) -> None:
        if self.notebook.select() == str(self.compilation_tab) and not self.compilation_busy:
            self._refresh_compilation_sessions()

    def _refresh_compilation_sessions(self) -> None:
        root = Path(self.compilation_root_var.get())
        self.compilation_sessions = discover_session_folders(root)
        for item in self.compilation_tree.get_children():
            self.compilation_tree.delete(item)
        valid_sessions: list[Path] = []
        skipped = 0
        for cycle_number, session_dir in enumerate(self.compilation_sessions, start=1):
            try:
                summary = read_session_summary(session_dir)
            except (OSError, ValueError, json.JSONDecodeError):
                skipped += 1
                continue
            valid_sessions.append(session_dir)
            timestamp = summary["timestamp"].replace("T", " ")[:19]
            self.compilation_tree.insert("", "end", values=(
                cycle_number,
                summary["session_name"],
                timestamp,
                summary["serial_number"],
                summary["profile_short"],
                summary["repetition"],
                summary["fan_mode"],
                summary["recovery_result"],
                summary["quantitative_result"],
                summary["overall_result"],
            ))
        self.compilation_sessions = valid_sessions
        generator_ready = excel_generator_available()
        self.compilation_generate_button.configure(state="normal" if valid_sessions and generator_ready else "disabled")
        skipped_text = f" {skipped} incomplete or unreadable folder(s) were skipped." if skipped else ""
        generator_text = "" if generator_ready else " Excel generator unavailable; the session list is still current."
        self.compilation_status_var.set(f"{len(valid_sessions)} valid session(s) detected.{skipped_text}{generator_text}")

    def _generate_compilation(self) -> None:
        if not self.compilation_sessions:
            messagebox.showwarning("Session Compilation", "Select a folder containing completed AMT205 sessions.")
            return
        root = Path(self.compilation_root_var.get())
        output = root / "compilations" / f"AMT205_Compilation_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        report = output / "AMT205_Thermal_Session_Compilation.xlsx"
        self.compilation_busy = True
        self.compilation_generate_button.configure(state="disabled")
        self.compilation_open_folder_button.configure(state="disabled")
        self.compilation_open_report_button.configure(state="disabled")
        self.compilation_status_var.set("Generating the consolidated workbook and comparison graph…")

        def worker() -> None:
            try:
                workbook, preview = create_session_compilation(self.compilation_sessions, report, root)
                self.messages.put(("compilation_report", (workbook, preview, output)))
            except Exception as exc:
                self.messages.put(("compilation_error", str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def _load_interfaces(self) -> None:
        def worker() -> None:
            try:
                interfaces = list_interfaces()
                route_alias = routed_interface_alias()
                preferred = preferred_interface_index(interfaces, route_alias)
                self.messages.put(("interfaces", (interfaces, preferred, route_alias)))
            except Exception as exc:
                self.messages.put(("error", str(exc)))
        threading.Thread(target=worker, daemon=True).start()

    def _metadata(self) -> dict:
        ambient = float(self.ambient_var.get().replace(",", "."))
        smart_min = float(self.smart_min_var.get().replace(",", "."))
        smart_max = float(self.smart_max_var.get().replace(",", "."))
        max_value = float(self.max_value_var.get().replace(",", "."))
        if not smart_min <= smart_max <= max_value:
            raise ValueError("Expected Min. ≤ Max. ≤ Max. Value.")
        equipment = self.config_data["equipment"]
        return {
            "profile": next(key for key, label in PROFILE_LABELS.items() if label == self.profile_var.get()),
            "fan_mode": "MAX" if self.max_power_var.get() else "NORMAL",
            "fan_mode_required": "MAX" if self.max_power_var.get() else "NORMAL",
            "injection_frequency_hz": float(self.config_data.get("test_scope", {}).get("injection_frequency_hz", 50.0)),
            "serial_number": self.serial_var.get().strip(),
            "ambient_c": ambient, "repetition": int(self.repetition_var.get()),
            "smart_delay_enabled": self.smart_delay_var.get(),
            "smart_delay_min_c": smart_min,
            "smart_delay_max_c": smart_max,
            "maximum_temperature_setting_c": max_value,
            "earth_enabled": self.earth_enable_var.get(),
            "compare_feedback": self.feedback_var.get(), "notes": "", **equipment,
        }

    def toggle_capture(self) -> None:
        if self.source_mode.get() == "PCAP File":
            inputs = self._session_inputs()
            if inputs is None:
                return
            metadata, active_config = inputs
            if not self.current_source:
                self._choose_pcap()
            if not self.current_source:
                return
            self.live_watchdog.stop()
            self.current_output = Path(self.output_base_var.get()) / default_session_name()
            self._reset_session(metadata, active_config)
            self.start_button.configure(state="disabled")
            self.source_combo.configure(state="disabled")
            self.status_var.set("Analyzing capture…")
            threading.Thread(target=self._analyze_file, args=(self.current_source,), daemon=True).start()
        elif self.live_capture is None:
            self._connect_live()
        else:
            self._disconnect_live()

    def _session_inputs(self) -> tuple[dict, dict] | None:
        try:
            metadata = self._metadata()
        except ValueError:
            self.notebook.select(self.settings_tab)
            messagebox.showerror("Invalid Session Data", "Check Ambient Temperature, Min., Max. and Max. Value.")
            return None
        active_config = session_config(self.config_data)
        if metadata["profile"] == "custom":
            active_config["profiles"]["custom"] = deepcopy(self.custom_profile)
        self._save_preferences()
        return metadata, active_config

    def _connect_live(self) -> None:
        inputs = self._session_inputs()
        if inputs is None:
            return
        selection = self.interface_combo.current()
        if selection < 0 or selection >= len(self.interfaces):
            messagebox.showwarning("Network Interface", "Select the interface connected to the test set.")
            return
        metadata, active_config = inputs
        self._reset_session(metadata, active_config)
        self.live_interface_number = self.interfaces[selection][0]
        self.live_capture = LiveCapture(
            self.live_interface_number,
            self._packet_callback,
            lambda text: self.messages.put(("error", text)),
        )
        self.live_capture.start()
        self.live_watchdog.start(time.monotonic())
        self.card_vars["state"].set("CONNECTING")
        self.start_button.configure(text="Disconnect / F5")
        self.record_button.configure(text="Start Recording / F6", state="disabled")
        self.source_combo.configure(state="disabled")
        self.interface_combo.configure(state="disabled")
        self.refresh_button.configure(state="disabled")
        self.status_var.set("Live Monitor connected. Waiting for the next AMT205 temperature packet…")
        self._set_next_step(
            title="Waiting for AMT205 data",
            instruction="Keep the connection active until the first live temperature packet is received.",
            detail="Start Recording becomes available after live readings are confirmed.",
        )

    def _disconnect_live(self) -> None:
        if self.raw_recorder is not None:
            if not self._stop_recording():
                return
        capture = self.live_capture
        if capture is None:
            return
        relative_s = self.session.samples[-1].reading.relative if self.session and self.session.samples else 0.0
        if self.session:
            event = self.session.add_monitor_event(
                "monitor_disconnected",
                "INFO",
                "Live Monitor disconnected by operator.",
                relative_s,
            )
            self._show_event(event)
        self.live_watchdog.stop()
        capture.stop()
        self.live_capture = None
        self.live_interface_number = None
        self._clear_disconnected_readings()
        self.start_button.configure(text="Connect / F5", state="normal")
        self.record_button.configure(text="Start Recording / F6", state="disabled")
        self.source_combo.configure(state="readonly")
        self.interface_combo.configure(state="readonly")
        self.refresh_button.configure(state="normal")
        self.status_var.set("Live Monitor disconnected. Temperatures are no longer live.")
        self._set_next_step(
            title="AMT205 disconnected",
            instruction="Press Connect / F5 when the test set is ready.",
            detail="No temperature shown on the Monitor is considered live while disconnected.",
        )

    def toggle_recording(self) -> None:
        if self.source_mode.get() != "Live Monitor":
            return
        if self.raw_recorder is not None:
            self._stop_recording()
        else:
            self._start_recording()

    def _start_recording(self) -> None:
        if self.live_capture is None or self.live_interface_number is None:
            messagebox.showwarning("Live Monitor", "Connect to the AMT205 before starting a recording.")
            return
        if self.live_watchdog.disconnected or self.live_watchdog.last_seen_at is None:
            messagebox.showwarning("AMT205 Connection", "Wait for live temperature readings before starting a recording.")
            return
        if self.report_busy:
            return
        inputs = self._session_inputs()
        if inputs is None:
            return
        metadata, active_config = inputs
        output = Path(self.output_base_var.get()) / default_session_name()
        raw_pcap = output / "evidence" / "original_capture.pcapng"
        recorder = RawRecorder(self.live_interface_number, raw_pcap)
        recording_session = TestSession(active_config, metadata)
        start_event = recording_session.add_monitor_event(
            "recording_started",
            "INFO",
            "Evidence recording started by operator.",
            0.0,
        )
        try:
            recorder.start()
        except Exception as exc:
            messagebox.showerror("Recording", str(exc))
            return
        self.raw_recorder = recorder
        self.recording_session = recording_session
        self.recording_origin_relative = None
        self.auto_finalize_pending = False
        self.current_output = output
        self.current_report = None
        self.open_report_button.configure(state="disabled")
        self.open_folder_button.configure(state="disabled")
        self._show_event(start_event)
        self.record_button.configure(text="Stop Recording / F6", state="normal")
        self.status_var.set("Recording evidence. Live Monitor will remain connected when recording stops.")
        self._set_next_step(
            title="Waiting for recorded data",
            instruction="Keep injection OFF while the first recorded temperature packet is received.",
            detail="The automatic initial baseline must be confirmed inside this recording. Injection stabilization uses the approved internal range and trend rule.",
        )

    def _stop_recording(self, *, automatic: bool = False) -> bool:
        recorder = self.raw_recorder
        recording_session = self.recording_session
        output = self.current_output
        if recorder is None or recording_session is None or output is None:
            return False
        workflow_step = recording_session.workflow_step
        if automatic and workflow_step.code not in recording_session.workflow.TERMINAL_CODES:
            self.auto_finalize_pending = False
            return False
        if not automatic and workflow_step.code not in recording_session.workflow.TERMINAL_CODES:
            if not messagebox.askyesno(
                "Minimum Evidence Not Complete",
                f"{workflow_step.title}\n\n{workflow_step.instruction}\n\nStop now and generate an incomplete or inconclusive report?",
                parent=self,
            ):
                return False
        self.status_var.set("Stopping evidence recording and generating the report…")
        recorder.stop()
        stop_relative = recording_session.samples[-1].reading.relative if recording_session.samples else 0.0
        stop_event = recording_session.add_monitor_event(
            "recording_stopped",
            "INFO",
            "Evidence recording stopped automatically after thermal recovery." if automatic else "Evidence recording stopped by operator.",
            stop_relative,
            {"automatic": automatic},
        )
        self._show_event(stop_event)
        self.raw_recorder = None
        self.recording_session = None
        self.recording_origin_relative = None
        self.auto_finalize_pending = False
        self.report_busy = True
        self.record_button.configure(text="Generating Report…", state="disabled")
        threading.Thread(
            target=self._finalize,
            args=(recording_session, output, recorder.raw_pcap),
            daemon=True,
        ).start()
        self._set_next_step(
            title="Generating report",
            instruction="Wait until the Excel report and evidence bundle are complete.",
            detail="Live Monitor remains connected during report generation.",
        )
        return True

    def _reset_session(self, metadata: dict, active_config: dict) -> None:
        self.session = TestSession(active_config, metadata, on_update=lambda item: self.messages.put(("snapshot", item)))
        self.recording_session = None
        self.recording_origin_relative = None
        self.auto_finalize_pending = False
        self.event_tree.delete(*self.event_tree.get_children())
        for name, _ in TEMPERATURE_FIELDS:
            self.sensor_tree.set(name, "temperature", "—")
        for variable, value in (("state", "WAITING"), ("elapsed", "—"), ("voltage", "—"), ("current", "—"), ("internal", "—"), ("result", "Overall: INCONCLUSIVE\nThermal: INCONCLUSIVE\nElectrical: NOT ASSESSED")):
            self.card_vars[variable].set(value)
        self.observed_fan_var.set("Fan Mode observed: not identified")
        self.open_report_button.configure(state="disabled")
        self.open_folder_button.configure(state="disabled")
        self.current_report = None
        self._set_next_step(
            title="Waiting for data",
            instruction="Connect to the AMT205 or analyze the selected capture.",
            detail="Workflow guidance will appear after decoded temperature readings are available.",
        )

    def _packet_callback(self, packet) -> None:
        if self.session:
            before = len(self.session.events)
            self.session.process(packet)
            for event in self.session.events[before:]:
                self.messages.put(("event", event))
        if self.recording_session:
            if self.recording_origin_relative is None:
                self.recording_origin_relative = packet.relative
            recording_packet = replace(packet, relative=max(0.0, packet.relative - self.recording_origin_relative))
            self.recording_session.process(recording_packet)
            workflow = self.recording_session.workflow_step
            if (
                should_auto_finalize(self.recording_session.config, workflow.code)
                and not self.auto_finalize_pending
            ):
                self.auto_finalize_pending = True
                event = self.recording_session.add_monitor_event(
                    "automatic_finalization",
                    "INFO",
                    "Thermal recovery workflow complete; automatic report generation requested.",
                    recording_packet.relative,
                    {"workflow_code": workflow.code},
                )
                self.messages.put(("event", event))
                self.messages.put(("auto_finalize", None))

    def _analyze_file(self, path: Path) -> None:
        try:
            for packet in iter_pcap(path):
                self._packet_callback(packet)
            if self.session and self.current_output:
                self.messages.put(("finalize", (self.session, self.current_output, path)))
        except Exception as exc:
            self.messages.put(("error", str(exc)))

    def _finalize(self, session: TestSession, output: Path, source: Path) -> None:
        try:
            evidence = output / "evidence"
            bundle = session.save(evidence, source)
        except Exception as exc:
            self.messages.put(("error", str(exc)))
            return
        try:
            report = create_excel(bundle, output / "AMT205_Thermal_Test_Report.xlsx")
            self.messages.put(("report", (report, output)))
        except Exception as exc:
            self.messages.put(("report_unavailable", (output, str(exc))))

    def _apply_snapshot(self, snapshot: SampleSnapshot) -> None:
        reading = snapshot.reading
        first_live_reading = self.live_capture is not None and self.live_watchdog.last_seen_at is None
        restored = self.live_watchdog.observe(time.monotonic()) if self.live_capture is not None else False
        if restored and self.session:
            event = self.session.add_monitor_event(
                "connection_restored",
                "INFO",
                "AMT205 connection restored; temperature updates resumed.",
                reading.relative,
            )
            self._show_event(event)
            if self.recording_session:
                recording_relative = self.recording_session.samples[-1].reading.relative if self.recording_session.samples else 0.0
                self.recording_session.add_monitor_event(
                    "connection_restored",
                    "INFO",
                    "AMT205 connection restored; temperature updates resumed.",
                    recording_relative,
                )
            self.status_var.set("AMT205 connection restored. Live temperatures are updating again.")
        elif first_live_reading:
            self.status_var.set("AMT205 connected. Live temperatures are updating; recording can now be started.")
        if self.live_capture is not None and self.raw_recorder is None and not self.report_busy:
            self.record_button.configure(text="Start Recording / F6", state="normal")
        self.card_vars["state"].set("INJECTION ACTIVE" if reading.executing else "IDLE")
        self.card_vars["elapsed"].set("—" if reading.profile_elapsed_s is None else f"{reading.profile_elapsed_s:.3f} s")
        hottest_voltage = max(VOLTAGE_SENSORS, key=reading.temperatures.__getitem__)
        hottest_current = max(CURRENT_SENSORS, key=reading.temperatures.__getitem__)
        self.card_vars["voltage"].set(f"{reading.temperatures[hottest_voltage]:.1f} °C\n{hottest_voltage}")
        self.card_vars["current"].set(f"{reading.temperatures[hottest_current]:.1f} °C\n{hottest_current}")
        hottest_internal = max(INTERNAL_SENSORS, key=reading.temperatures.__getitem__)
        self.card_vars["internal"].set(f"{reading.temperatures[hottest_internal]:.1f} °C\n{hottest_internal}")
        fan_label = "Max Power" if snapshot.fan_mode == "MAX" else "Normal" if snapshot.fan_mode == "NORMAL" else snapshot.fan_mode
        self.observed_fan_var.set(f"Fan Mode observed: {fan_label}")
        for name, temperature in reading.temperatures.items():
            self.sensor_tree.set(name, "temperature", f"{temperature:.1f} °C")
        evaluation_session = self.recording_session or self.session
        if evaluation_session:
            result = evaluation_session.evaluation()
            self.card_vars["result"].set(
                f"Overall: {result.status}\nThermal: {result.thermal_status}\nElectrical: {result.electrical_status}"
            )
        if self.recording_session is not None:
            self._set_next_step(self.recording_session.workflow_step)
        elif self.source_mode.get() == "PCAP File" and self.session is not None:
            self._set_next_step(self.session.workflow_step)
        elif self.live_capture is not None:
            self._set_next_step(
                title="Ready to record",
                instruction="Press Start Recording / F6 when the test conditions are ready to be documented.",
                detail="The automatic initial baseline must be captured after recording starts. It prepares the test but does not determine injection-temperature acceptance.",
            )

    def _show_event(self, event) -> None:
        self.event_tree.insert("", "end", values=(f"{event.relative:.3f} s", event.severity, event.message))
        self.event_tree.yview_moveto(1)

    def _clear_disconnected_readings(self) -> None:
        for name, _ in TEMPERATURE_FIELDS:
            self.sensor_tree.set(name, "temperature", "—")
        self.card_vars["state"].set("DISCONNECTED")
        self.card_vars["elapsed"].set("—")
        self.card_vars["voltage"].set("—")
        self.card_vars["current"].set("—")
        self.card_vars["internal"].set("—")
        self.observed_fan_var.set("Fan Mode observed: not identified")
        self._set_next_step(
            title="AMT205 disconnected",
            instruction="Wait for the temperature stream to return or reconnect the Live Monitor.",
            detail="The last temperature readings were cleared because they are no longer live.",
        )

    def _check_live_connection(self) -> None:
        if self.live_capture is None or not self.live_watchdog.poll(time.monotonic()):
            return
        self._clear_disconnected_readings()
        relative_s = self.live_watchdog.timeout_s
        if self.session and self.session.samples:
            relative_s += self.session.samples[-1].reading.relative
        if self.session:
            event = self.session.add_monitor_event(
                "connection_lost",
                "WARNING",
                "AMT205 disconnected; temperature updates stopped.",
                relative_s,
                {"timeout_s": self.live_watchdog.timeout_s},
            )
            self._show_event(event)
        if self.recording_session:
            recording_relative = self.recording_session.samples[-1].reading.relative if self.recording_session.samples else 0.0
            self.recording_session.add_monitor_event(
                "connection_lost",
                "WARNING",
                "AMT205 disconnected; temperature updates stopped.",
                recording_relative + self.live_watchdog.timeout_s,
                {"timeout_s": self.live_watchdog.timeout_s},
            )
        if self.raw_recorder is None:
            self.record_button.configure(text="Start Recording / F6", state="disabled")
        self.status_var.set("AMT205 disconnected. Waiting for the temperature stream to return…")

    def _poll_messages(self) -> None:
        try:
            while True:
                kind, value = self.messages.get_nowait()
                if kind == "interfaces":
                    self.interfaces, preferred, route_alias = value
                    labels = []
                    for index, (number, description) in enumerate(self.interfaces):
                        label = f"{number} — {interface_alias(description)}"
                        if index == preferred:
                            label += " — AMT route"
                        labels.append(label)
                    self.interface_combo["values"] = labels
                    if preferred is not None:
                        self.interface_combo.current(preferred)
                        self.status_var.set(f"AMT network route detected: {labels[preferred]}.")
                    else:
                        self.interface_combo.set("")
                        detail = f" Route reported by Windows: {route_alias}." if route_alias else ""
                        self.status_var.set(f"Select the network interface connected to the test set.{detail}")
                elif kind == "snapshot":
                    self._apply_snapshot(value)
                elif kind == "event":
                    self._show_event(value)
                elif kind == "finalize":
                    self.status_var.set("Generating consolidated report…")
                    self.report_busy = True
                    threading.Thread(target=self._finalize, args=value, daemon=True).start()
                elif kind == "auto_finalize":
                    if self.raw_recorder is not None and self.recording_session is not None:
                        self._stop_recording(automatic=True)
                elif kind == "report":
                    self.current_report, self.current_output = value
                    self.report_busy = False
                    self.open_report_button.configure(state="normal")
                    self.open_folder_button.configure(state="normal")
                    if self.live_capture is not None:
                        self.start_button.configure(text="Disconnect / F5", state="normal")
                        record_state = "disabled" if self.live_watchdog.disconnected else "normal"
                        self.record_button.configure(text="Start Recording / F6", state=record_state)
                        if self.live_watchdog.disconnected:
                            self.status_var.set("Recording completed. Waiting for the AMT205 stream to return.")
                        else:
                            self.status_var.set("Recording completed. Live Monitor remains connected.")
                            self._set_next_step(
                                title="Ready for another recording",
                                instruction="Review the report or press Start Recording / F6 for the next repetition.",
                                detail="Live temperatures remain available; each recording creates a separate evidence bundle.",
                            )
                    else:
                        button_text = "Connect / F5" if self.source_mode.get() == "Live Monitor" else "Analyze / F5"
                        self.start_button.configure(text=button_text, state="normal")
                        self.source_combo.configure(state="readonly")
                        self.status_var.set("Session completed. Use Open Report or Open Output Folder.")
                        self._set_next_step(
                            title="Analysis completed",
                            instruction="Open the report and review the reconstructed workflow and result.",
                            detail="The source capture and decoded evidence are preserved in the session folder.",
                        )
                    self._refresh_compilation_sessions()
                elif kind == "report_unavailable":
                    self.current_output, report_error = value
                    self.current_report = None
                    self.report_busy = False
                    self.open_report_button.configure(state="disabled")
                    self.open_folder_button.configure(state="normal")
                    if self.live_capture is not None:
                        self.start_button.configure(text="Disconnect / F5", state="normal")
                        record_state = "disabled" if self.live_watchdog.disconnected else "normal"
                        self.record_button.configure(text="Start Recording / F6", state=record_state)
                    else:
                        button_text = "Connect / F5" if self.source_mode.get() == "Live Monitor" else "Analyze / F5"
                        self.start_button.configure(text=button_text, state="normal")
                        self.source_combo.configure(state="readonly")
                    self.status_var.set(f"Session evidence saved: {self.current_output}. Excel report unavailable.")
                    self._set_next_step(
                        title="Session saved without Excel report",
                        instruction="Use Open Output Folder to access the preserved evidence.",
                        detail="The capture, decoded readings, events and metadata were saved successfully. " + report_error,
                    )
                    self._refresh_compilation_sessions()
                    messagebox.showwarning(
                        "Excel Report Unavailable",
                        f"The session evidence was saved successfully in:\n{self.current_output}\n\n{report_error}",
                    )
                elif kind == "compilation_report":
                    self.compilation_report, self.compilation_preview, self.compilation_output = value
                    self.compilation_busy = False
                    self.compilation_generate_button.configure(state="normal")
                    self.compilation_open_folder_button.configure(state="normal")
                    self.compilation_open_report_button.configure(state="normal")
                    self.compilation_status_var.set(f"Compilation completed: {len(self.compilation_sessions)} session(s).")
                elif kind == "compilation_error":
                    self.compilation_busy = False
                    self.compilation_generate_button.configure(state="normal" if self.compilation_sessions else "disabled")
                    self.compilation_status_var.set(f"Compilation error: {value}")
                    messagebox.showerror("Session Compilation", str(value))
                elif kind == "error":
                    self.report_busy = False
                    self.start_button.configure(text="Disconnect / F5" if self.live_capture is not None else "Connect / F5" if self.source_mode.get() == "Live Monitor" else "Analyze / F5", state="normal")
                    if self.live_capture is not None and self.raw_recorder is None:
                        record_state = "disabled" if self.live_watchdog.disconnected else "normal"
                        self.record_button.configure(text="Start Recording / F6", state=record_state)
                    self.status_var.set(f"Error: {value}")
                    messagebox.showerror("AMT205 Thermal Validator", str(value))
        except queue.Empty:
            pass
        self._check_live_connection()
        self.after(100, self._poll_messages)

    def _open_report(self) -> None:
        if self.current_report and self.current_report.exists():
            os.startfile(str(self.current_report))

    def _open_folder(self) -> None:
        if self.current_output and self.current_output.exists():
            os.startfile(str(self.current_output))

    def _open_compilation_report(self) -> None:
        if self.compilation_report and self.compilation_report.exists():
            os.startfile(str(self.compilation_report))

    def _open_compilation_folder(self) -> None:
        if self.compilation_output and self.compilation_output.exists():
            os.startfile(str(self.compilation_output))

    def _close(self) -> None:
        if self.raw_recorder is not None:
            messagebox.showwarning("Recording Active", "Stop Recording and wait for the report before closing the application.")
            return
        if self.report_busy:
            messagebox.showwarning("Report in Progress", "Wait for the report to finish before closing the application.")
            return
        if self.compilation_busy:
            messagebox.showwarning("Compilation in Progress", "Wait for the session compilation to finish before closing the application.")
            return
        if self.live_capture:
            if not messagebox.askyesno("Exit", "Live Monitor is connected. Disconnect and exit?"):
                return
            self.live_capture.stop()
        self._save_preferences()
        self.live_watchdog.stop()
        self.destroy()


def main() -> None:
    Application().mainloop()


if __name__ == "__main__":
    main()

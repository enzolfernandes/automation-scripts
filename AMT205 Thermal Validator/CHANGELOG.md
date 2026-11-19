# Changelog

## 0.9.3 — 2026-09-02

- supports non-destructive operator corrections to a saved session profile;
- recalculates the effective historical reference, margin and quantitative result from the corrected profile while preserving the originally recorded profile;
- records the confirmed correction of session `AMT205_20260901_143903` from Current 75% to Current 100%.

## 0.9.2 — 2026-09-02

- added a fast direct XlsxWriter fallback for individual reports and session compilations;
- removed the exclusive dependency on the Codex spreadsheet runtime for Excel output;
- preserved the seven-sheet report structure and native thermal trend chart;
- added report regeneration support for previously saved evidence bundles.

## 0.9.1 — 2026-09-01

- decodes the new 129-byte AMPro periodic request and confirms the unchanged Fan Mode field at offset 18 against session `AMT205_20260901_135122`;
- preserves a completed session as a successful evidence-only result when Excel generation is unavailable, enables Open Output Folder and clearly separates the report warning from capture failure;
- refreshes Session Compilation automatically when its tab opens and after every completed or evidence-only session;
- applies non-destructive sidecar corrections when compiling legacy sessions, including the previously confirmed Max Power correction for `AMT205_20260827_114956`;
- shows the corrected Fan Mode and quantitative comparison while preserving the original immutable metadata;
- disables compilation generation when the Excel component is unavailable, while continuing to show the current session list.

## 0.9.0 — 2026-09-01

- enables selection of Fan Mode Max Power or Normal and restores the saved choice on the next startup;
- keeps Max Power as the only route with approved automatic acceptance references; Normal-mode sessions are explicitly comparative and cannot be automatically approved by Max Power criteria;
- adopts fan-specific operational stabilization targets: 25 °C for Max Power and 33 °C for Normal, with a default ±1 °C band held for 120 s;
- replaces fixed 10 s automatic finalization with post-injection thermal recovery recording;
- records recovery target, starting and ending temperatures, time to target, confirmation hold and timeout result in session evidence and Excel reports;
- treats the selected Session Compilation folder as a successive test series, numbers cycles chronologically and records the interval since the previous session;
- adds cycle and recovery fields to the compilation interface and consolidated workbook.

## 0.8.0 — 2026-08-31

- adds a **Session Compilation** tab that discovers completed AMT205 evidence bundles below a selected folder;
- creates one Excel workbook with a session summary, a common-time comparison chart and long-form injection readings;
- exports a PNG copy of the consolidated thermal graph beside the workbook;
- keeps the source sessions read-only and compiles their existing `metadata.json` and `readings.csv` evidence without reinterpreting packet captures.
- corrects the Max Power historical-time factor to 1× because the adopted 50%, 75% and 100% references were themselves recorded with Max Power; saved 3× defaults from older versions are migrated automatically;
- distinguishes the compilation's quantitative comparison from the result originally recorded by an older validator version.

## 0.7.1 — 2026-08-27

- corrects the updated 124-byte AMPro request Fan Mode field from offset 23 to offset 18;
- confirms the corrected field against a Normal capture and two Max Power sessions, preventing false Fan Mode mismatch warnings;
- keeps the legacy 110-byte Fan Mode decoder unchanged.

## 0.7.0 — 2026-08-27

- limits the approved stress-test route to Fan Mode (Max Power); Normal Fan Mode is no longer selectable for new sessions;
- labels all standard current and voltage profiles with the required Max Power condition;
- records the historical comparison frequency as 50 Hz in session metadata and Excel Configuration;
- migrates remembered settings and the command-line interface to Max Power automatically while retaining Normal decoding for legacy evidence.

## 0.6.0 — 2026-08-27

- adds the 75% (89.93 s) and 100% (57.49 s) current reference profiles alongside the existing 25% and 50% profiles;
- applies the configurable 3× historical-time multiplier for Fan Mode Max Power;
- approves the internal 120 s thermal-stability rule (range ≤1.0 °C, absolute slope ≤0.20 °C/min, maximum control temperature 52 °C with a 52–55 °C review band);
- reports Thermal, Electrical and Overall results independently, so `IA1 Other` is treated as an electrical endpoint and cannot falsely establish thermal compliance;
- records stability range and slope in the session evidence and Excel report.

## 0.5.7 — 2026-08-27

- confirms the updated 1138-byte injection response and its 26-byte progress trailer from the synchronized `AMT205_20260827_091852` capture;
- decodes duplicated elapsed microseconds and progress statuses 5, 6 and 14 in the updated firmware;
- records the device State Time (`214.308 s` in the confirming capture) instead of reporting zero;
- classifies status 14 as the operator-confirmed `IA1 Other` / Historical Stress Endpoint for current-reference profiles, while preserving internal code 6 as Thermal Error;
- keeps the 25% result as `REVIEW` when that endpoint occurs within the 55 °C review limit and no stabilization duration has been approved;
- preserves local wall-clock timestamps in Excel instead of converting the displayed time to UTC;
- adds regression coverage for the updated injection trailer and endpoint interpretation.

## 0.5.6 — 2026-08-27

- replaces the fixed initial-temperature gate with an automatic operational baseline while injection is OFF;
- uses configurable provisional defaults derived from the 12 min 51 s `capturaTermal.pcapng` observation: 24–34 °C envelope, 180 s minimum, 0.5 °C maximum range and ±0.10 °C/min maximum trend;
- resets the baseline after Fan Mode mismatch, temperature-envelope violation, excessive range or a packet gap longer than 5 s;
- reports the baseline temperature, hottest V/I sensor, observation time, range and trend in CSV, metadata and Excel;
- explicitly separates initial operational readiness from injection-temperature stability and manufacturer acceptance;
- adds all baseline parameters to **Settings → Acceptance Criteria** for future adjustment.

## 0.5.5 — 2026-08-25

- adds automatic decoding of the updated 1112-byte AMT205 status response while preserving the legacy 1067-byte layout;
- detects the extended layout through its 1112-byte length marker at packet offsets 6–7;
- applies the confirmed +10-byte shift to temperature, execution and internal-error fields;
- decodes the updated 124-byte periodic request and its relocated Fan Mode field while retaining the 110-byte request;
- prevents unknown advertised layouts or implausible temperatures from appearing as live readings;
- keeps the extended-layout progress trailer provisional until a synchronized injection capture confirms its new position;
- updates the protocol map to `AMT205-V8-2026-08-24.4`.

## 0.5.4 — 2026-08-25

- removes the unsupported 300 s assumption from initial conditioning and injection acceptance;
- confirms only the required initial temperature when no stabilization duration is approved;
- prevents PASS or FAIL conclusions based on provisional diagnostic stability windows;
- adds read-only 30 s, 60 s and 120 s curve diagnostics using the provisional 1.0 °C range;
- marks the stability duration as `Not approved` and the range as `Provisional diagnostic` in Excel;
- revises Historical Stress Endpoint rationale so `IA1 Other` is reported with observed time and temperature, without claiming that a 300 s window was interrupted;
- permits a future duration to be enabled explicitly in Acceptance Criteria;
- migrates saved 0.5.3 settings so the obsolete 300 s value cannot return automatically.

## 0.5.3 — 2026-08-25

- classifies latched non-thermal progress status 14 as a configurable Historical Stress Endpoint for current reference profiles;
- evaluates the endpoint against the 50% historical time or returns REVIEW for an in-range 25% temperature interrupted before formal stabilization;
- preserves unidentified status 14 events when no latched error accompanies the termination;
- records the endpoint classification in Events, workflow traceability and Excel formulas;
- automatically saves and restores the previous source, profile, equipment, environment, AMPro and output-location settings;
- excludes user-interface preferences from immutable session evidence and writes the settings file atomically.

## 0.5.2 — 2026-08-25

- corrected hottest mapped internal temperature to use only Main Board 1 and Main Board 2;
- reclassified progress status 14 as a generic AMPro error termination rather than Thermal Error;
- requires latched internal code 6 before classifying an event as Thermal Error;
- marks unidentified error terminations, such as the observed `IA1 Other`, as inconclusive and requiring repetition;
- updated Excel formulas, Events, workflow guidance and protocol documentation for the refined error mapping.

## 0.5.1 — 2026-08-25

- added optional passive automatic finalization after injection OFF and the complete post-test evidence window;
- records the automatic-finalization request and automatic recording stop in session Events;
- keeps all electrical control with AMPro and never transmits a command to the test set;
- limits only diagnostic preview images for long Readings and Events sheets while preserving every row in the Excel report;
- exports the Excel workbook before non-critical preview generation so evidence remains available if a preview cannot be rendered.

## 0.5.0 — 2026-08-25

- added a packet-driven `Next Step` panel for initial conditioning, injection and post-test evidence;
- added conservative minimum countdowns that round fractions upward and require the full configured duration;
- blocks initial-stability progress when temperature or Fan Mode is outside the selected condition;
- warns when injection starts before initial confirmation, is interrupted or ends before the minimum reference;
- guides Current 50% through the 179.4 s historical reference and 185 s recommended duration;
- records workflow transitions in Events and the final workflow state in report traceability;
- added configurable minimum post-test evidence duration, defaulting to 10 s;
- warns before stopping a recording whose minimum evidence sequence is incomplete.

## 0.4.1 — 2026-08-25

- fixed `Open Output Folder` being compressed by long report paths in the status bar;
- reserved stable widths for both footer action buttons;
- shortened completion messages while preserving full report access through the buttons.

## 0.4.0 — 2026-08-25

- separated live connection from evidence recording;
- added independent `Connect / Disconnect` and `Start / Stop Recording` controls with F5 and F6 shortcuts;
- keeps Live Monitor active after a recording is stopped and its report is generated;
- allows repeated recording sessions without reconnecting to the AMT205;
- starts recorded relative time at zero and preserves recording start/stop events;
- clears live readings when the stream is lost or the operator disconnects.

## 0.3.1 — 2026-08-25

- detects loss of the AMT205 temperature stream after five seconds without a status packet;
- clears stale temperatures and changes the live state to `DISCONNECTED`;
- records connection loss and restoration in the on-screen event history and generated report;
- resumes live values automatically when AMT205 status packets return.

## 0.3.0 — 2026-08-25

- simplified the initial screen to connection, live status, temperatures and events;
- moved session, report, AMPro and acceptance fields into a dedicated Settings tab;
- added in-application explanations for every setting and a complete Guide tab;
- separated Voltage, Current and Internal temperature groups in the live table;
- added independent Hottest Voltage, Hottest Current and Hottest Other indicators;
- added color-coded channel groups while preserving the hottest V/I acceptance rule.

## 0.2.1 — 2026-08-24

- fixed Live Monitor selecting the first Windows capture interface instead of the adapter routed to the AMT205;
- automatically detects and selects the adapter used to reach `192.168.1.41`;
- displays concise adapter names and marks the selected `AMT route`;
- verified idle monitoring with periodic temperature readings while no injection was active.

## 0.2.0 — 2026-08-24

- translated the application, generated report and operator messages to English;
- adopted AMPro terminology for Smart Delay, Min., Max., Max. Value, Earth and Fan Mode (Max Power);
- added selectable output location;
- added a configurable Free Profile with monitor-only and optional acceptance criteria;
- clarified and tested attachment to an already-running test for real-time temperatures;
- preserved the formal initial-condition safeguard when the beginning was not captured;
- renamed generated evidence and report files in English.

## 0.1.0 — 2026-08-24

Initial functional release.

- passive live capture with F5 shortcut;
- PCAP/PCAPNG import;
- decoding of ten known internal sensors;
- Fan Mode, execution, progress and Thermal Error detection;
- configurable criteria and conservative automatic evaluation;
- consolidated Excel report with traceability and charts;
- technical CSV/JSON evidence preservation;
- unit tests for the main protocol fields and criteria.

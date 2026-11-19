import fs from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";

const [inputPath, outputPath, previewDir] = process.argv.slice(2);
if (!inputPath || !outputPath || !process.env.ARTIFACT_TOOL_ENTRY) {
  throw new Error("Usage: report_builder.mjs <input.json> <output.xlsx> <previews>");
}
const { SpreadsheetFile, Workbook } = await import(pathToFileURL(process.env.ARTIFACT_TOOL_ENTRY).href);
const payload = JSON.parse(await fs.readFile(inputPath, "utf8"));
const { metadata, samples, events } = payload;

const workbook = Workbook.create();
const summary = workbook.worksheets.add("Summary");
const results = workbook.worksheets.add("Results");
const readings = workbook.worksheets.add("Readings");
const eventSheet = workbook.worksheets.add("Events");
const charts = workbook.worksheets.add("Charts");
const configSheet = workbook.worksheets.add("Configuration");
const trace = workbook.worksheets.add("Traceability");

const navy = "#17324D";
const blue = "#2878B5";
const teal = "#1B7F79";
const paleBlue = "#EAF3F8";
const paleGreen = "#E8F4EE";
const paleAmber = "#FFF3D6";
const paleRed = "#FBE8E7";
const gray = "#5C6B73";
const light = "#F5F7F9";
const white = "#FFFFFF";

function colLetter(index) {
  let value = index + 1;
  let result = "";
  while (value > 0) {
    const remainder = (value - 1) % 26;
    result = String.fromCharCode(65 + remainder) + result;
    value = Math.floor((value - 1) / 26);
  }
  return result;
}

function setTitle(sheet, title, subtitle, endCol = "H") {
  sheet.showGridLines = false;
  sheet.getRange(`A1:${endCol}2`).merge();
  sheet.getRange("A1").values = [[title]];
  sheet.getRange(`A1:${endCol}2`).format = {
    fill: navy, font: { bold: true, color: white, size: 18 },
    verticalAlignment: "center", horizontalAlignment: "left",
  };
  sheet.getRange(`A3:${endCol}3`).merge();
  sheet.getRange("A3").values = [[subtitle]];
  sheet.getRange(`A3:${endCol}3`).format = { fill: paleBlue, font: { color: gray, italic: true }, wrapText: true };
  sheet.getRange("A1:A3").format.rowHeight = 25;
}

function styleTable(sheet, range, headerRange) {
  headerRange.format = { fill: blue, font: { bold: true, color: white }, wrapText: true, verticalAlignment: "center" };
  range.format.borders = { preset: "inside", style: "thin", color: "#D9E1E6" };
  range.format.autofitColumns();
  range.format.autofitRows();
}

function localTimestampText(value) {
  if (value == null) return null;
  return String(value).replace("T", " ").replace(/(?:Z|[+-]\d{2}:\d{2})$/, "");
}

const session = metadata.session || {};
const configuration = metadata.configuration || {};
const profileKey = session.profile || "current_50";
const profile = configuration.profiles?.[profileKey] || {};
const evaluation = metadata.evaluation || {};
const stabilityApproved = configuration.stability?.window_approved === true && configuration.stability?.window_s != null;
const stabilitySlopeLimit = configuration.stability?.maximum_abs_slope_c_per_min ?? null;
const fanReferenceMultiplier = session.fan_mode === "MAX" ? Number(configuration.fan_mode?.max_power_historical_time_multiplier ?? 1) : 1;
const effectiveHistoricalReference = profile.historical_time_s == null ? null : Number(profile.historical_time_s) * fanReferenceMultiplier;
const effectiveRecommendedDuration = effectiveHistoricalReference == null ? null : effectiveHistoricalReference + Math.max(0, Number(profile.recommended_duration_s ?? profile.historical_time_s) - Number(profile.historical_time_s));
const stabilityDiagnostics = metadata.stability_diagnostics || [];
const baselineConfiguration = configuration.initial_baseline || {};
const testScope = configuration.test_scope || {};
const baseline = metadata.workflow?.initial_baseline || {};
const recoveryConfiguration = configuration.thermal_recovery || {};
const recovery = metadata.workflow?.thermal_recovery || {};
const historicalReferenceFanMode = String(testScope.historical_reference_fan_mode || "MAX").toUpperCase();
const criterionLabels = {
  monitor_only: "Monitor only — no automatic acceptance",
  historical_time: "Historical time reference",
  stable_temperature: "Stable temperature limits",
  reach_stability: "Reach stability without Thermal Error",
};
const criterionDisplay = criterionLabels[profile.criterion] || profile.criterion || "";

// Configuration is created before formulas that reference it.
setTitle(configSheet, "Applied Configuration", "Values used by this evaluation. Future changes must create a new session and preserve this evidence.", "D");
const configRows = [
  ["Field", "Value", "Unit", "Source"],
  ["Profile", profile.label || profileKey, "", "Session"],
  ["Profile ID", profileKey, "", "Configuration"],
  ["Criterion", criterionDisplay, "", "Configuration"],
  ["Session Fan Mode", session.fan_mode === "MAX" ? "Max Power" : "Normal", "", "Session"],
  ["Historical Reference Fan Mode", historicalReferenceFanMode === "MAX" ? "Max Power" : historicalReferenceFanMode, "", "Approved reference condition"],
  ["Injection Frequency", session.injection_frequency_hz ?? testScope.injection_frequency_hz ?? null, "Hz", "Historical reference condition"],
  ["Initial / Recovery Target — Max Power", configuration.initial_temperature_c?.max ?? null, "°C", "Operational criterion"],
  ["Initial / Recovery Target — Normal", configuration.initial_temperature_c?.normal ?? null, "°C", "Operational criterion"],
  ["Target Tolerance", configuration.initial_temperature_c?.tolerance ?? null, "°C", "Operational criterion"],
  ["Stability Window", stabilityApproved ? configuration.stability.window_s : "Not approved", "s", stabilityApproved ? "Approved configuration" : "Undefined"],
  ["Maximum Range Within Window", configuration.stability?.maximum_range_c ?? null, "°C", "Approved internal criterion"],
  ["Maximum Absolute Trend Within Window", stabilitySlopeLimit, "°C/min", "Approved internal criterion"],
  ["Fan Max Power Historical Multiplier", configuration.fan_mode?.max_power_historical_time_multiplier ?? 1, "×", "Approved internal criterion"],
  ["Base Historical Time Reference", profile.historical_time_s ?? null, "s", profile.historical_time_s ? "Legacy reference" : "N/A"],
  ["Effective Historical Time Reference", effectiveHistoricalReference, "s", effectiveHistoricalReference == null ? "N/A" : "Applied to this Fan Mode"],
  ["Effective Recommended Duration", effectiveRecommendedDuration, "s", effectiveRecommendedDuration == null ? "N/A" : "Operator guidance"],
  ["Pass Up To", profile.approved_max_c ?? null, "°C", profile.approved_max_c ? "Approved provisional criterion" : "N/A"],
  ["Review Up To", profile.investigate_max_c ?? null, "°C", profile.investigate_max_c ? "Approved provisional criterion" : "N/A"],
  ["Maximum Duration", profile.maximum_duration_s ?? profile.recommended_duration_s ?? null, "s", "Configuration"],
  ["Required Repetitions", configuration.repetitions?.required ?? null, "", "Approved"],
  ["Third Repetition Above Difference", configuration.repetitions?.third_if_difference_percent_above ?? null, "%", "Configurable proposal"],
  ["Thermal Error ends timer", configuration.event_rules?.thermal_error_always_stops_timer ? "Yes" : "No", "", "Configuration"],
  ["Status 14 as IA1 Other Electrical Endpoint", configuration.event_rules?.status_14_is_historical_stress_endpoint == null ? "Yes" : configuration.event_rules.status_14_is_historical_stress_endpoint ? "Yes" : "No", "", "Configuration"],
  ["Cooling ends timer if output is reduced", configuration.event_rules?.cooling_stops_timer_if_output_reduced ? "Yes" : "No", "", "Configuration"],
  ["Smart Delay ends timer if output is reduced", configuration.event_rules?.smart_delay_stops_timer_if_output_reduced ? "Yes" : "No", "", "Configuration"],
  ["Fallback Post-test Evidence", configuration.workflow?.post_test_evidence_s ?? 10, "s", "Used only when recovery recording is disabled"],
  ["Automatic Report Finalization", configuration.workflow?.auto_finalize_recording == null ? "Not recorded" : configuration.workflow.auto_finalize_recording ? "Yes" : "No", "", "Configuration"],
  ["Thermal Recovery Recording", recoveryConfiguration.enabled === false ? "Disabled" : "Enabled", "", "Operational evidence"],
  ["Thermal Recovery Hold", recoveryConfiguration.hold_s ?? 120, "s", "Operational evidence"],
  ["Thermal Recovery Maximum Duration", recoveryConfiguration.maximum_duration_s ?? 1800, "s", "Operational evidence"],
  ["Automatic Initial Baseline", baselineConfiguration.enabled === false ? "Disabled" : "Enabled", "", "Configuration"],
  ["Initial Baseline Uses Fan Mode Target", baselineConfiguration.use_fan_mode_target === false ? "No" : "Yes", "", "Operational rule"],
  ["Initial Baseline Minimum Observation", baselineConfiguration.minimum_observation_s ?? 120, "s", "Operational rule"],
  ["Initial Baseline Maximum Range", baselineConfiguration.maximum_range_c ?? 1, "°C", "Operational rule"],
  ["Initial Baseline Maximum Absolute Trend", baselineConfiguration.maximum_abs_slope_c_per_min ?? 0.2, "°C/min", "Operational rule"],
  ["Initial Baseline Maximum Packet Gap", baselineConfiguration.maximum_sample_gap_s ?? 5, "s", "Configuration"],
  ["Initial Baseline Evidence", baselineConfiguration.evidence || "Not recorded", "", "Capture-derived"],
];
const configRowByField = new Map(configRows.slice(1).map((row, index) => [row[0], 6 + index]));
const configValueCell = field => `'Configuration'!B${configRowByField.get(field)}`;
configSheet.getRange(`A5:D${4 + configRows.length}`).values = configRows;
styleTable(configSheet, configSheet.getRange(`A5:D${4 + configRows.length}`), configSheet.getRange("A5:D5"));
configSheet.getRange(`B6:B${4 + configRows.length}`).format.numberFormat = "0.000";
configSheet.freezePanes.freezeRows(5);

// Readings sheet.
setTitle(readings, "Decoded Thermal Readings", "Internal test-set data. CPU Temperature is not mapped. The control temperature is the hottest of the eight V/I sensors.", "AC");
const readingHeaders = samples.length ? Object.keys(samples[0]) : ["timestamp_iso", "relative_s", "frame"];
const readingMatrix = [readingHeaders, ...samples.map(row => readingHeaders.map(header => header === "timestamp_iso" ? localTimestampText(row[header]) : row[header] ?? null))];
readings.getRangeByIndexes(4, 0, readingMatrix.length, readingHeaders.length).values = readingMatrix;
const readingRange = readings.getRangeByIndexes(4, 0, readingMatrix.length, readingHeaders.length);
styleTable(readings, readingRange, readings.getRangeByIndexes(4, 0, 1, readingHeaders.length));
if (samples.length) readings.tables.add(`A5:${colLetter(readingHeaders.length - 1)}${5 + samples.length}`, true, "ReadingsTable");
readings.freezePanes.freezeRows(5);
readings.freezePanes.freezeColumns(3);
const numericTwo = ["relative_s", "profile_elapsed_s", "hottest_control_c", "hottest_internal_c", "stability_range_c", "stability_slope_c_per_min", "initial_target_c", "initial_baseline_c", "initial_baseline_range_c", "initial_baseline_slope_c_per_min", "initial_baseline_observation_s"];
for (const name of numericTwo) {
  const index = readingHeaders.indexOf(name);
  if (index >= 0 && samples.length) readings.getRangeByIndexes(5, index, samples.length, 1).format.numberFormat = "0.000";
}
const readingTimestampIndex = readingHeaders.indexOf("timestamp_iso");
if (readingTimestampIndex >= 0 && samples.length) {
  readings.getRangeByIndexes(5, readingTimestampIndex, samples.length, 1).format.numberFormat = "@";
  readings.getRangeByIndexes(4, readingTimestampIndex, samples.length + 1, 1).format.columnWidth = 24;
}
for (const name of ["initial_baseline_c", "initial_baseline_range_c", "initial_baseline_slope_c_per_min", "initial_baseline_observation_s"]) {
  const index = readingHeaders.indexOf(name);
  if (index >= 0) readings.getRangeByIndexes(4, index, readingMatrix.length, 1).format.columnWidth = 24;
}
readings.getRangeByIndexes(4, 0, 1, readingHeaders.length).format.wrapText = true;
readings.getRangeByIndexes(4, 0, 1, readingHeaders.length).format.rowHeight = 42;

// Events sheet.
setTitle(eventSheet, "Events and Transitions", "Events observed in packets. The validator does not send these commands.", "H");
const eventHeaders = events.length ? Object.keys(events[0]) : ["timestamp_iso", "relative_s", "frame", "kind", "severity", "message", "source", "details"];
const eventMatrix = [eventHeaders, ...events.map(row => eventHeaders.map(header => header === "timestamp_iso" ? localTimestampText(row[header]) : row[header] ?? null))];
eventSheet.getRangeByIndexes(4, 0, eventMatrix.length, eventHeaders.length).values = eventMatrix;
styleTable(eventSheet, eventSheet.getRangeByIndexes(4, 0, eventMatrix.length, eventHeaders.length), eventSheet.getRangeByIndexes(4, 0, 1, eventHeaders.length));
if (events.length) eventSheet.tables.add(`A5:${colLetter(eventHeaders.length - 1)}${5 + events.length}`, true, "EventsTable");
const eventLastRow = Math.max(6, 5 + events.length);
const eventWidths = [24, 13, 9, 22, 12, 55, 14, 38];
eventWidths.slice(0, eventHeaders.length).forEach((width, index) => {
  eventSheet.getRangeByIndexes(4, index, eventMatrix.length, 1).format.columnWidth = width;
});
eventSheet.getRange(`F5:G${eventLastRow}`).format.wrapText = true;
eventSheet.getRange(`H5:H${eventLastRow}`).format.wrapText = false;
eventSheet.getRange(`A5:G${eventLastRow}`).format.autofitRows();
const eventTimestampIndex = eventHeaders.indexOf("timestamp_iso");
if (eventTimestampIndex >= 0 && events.length) eventSheet.getRangeByIndexes(5, eventTimestampIndex, events.length, 1).format.numberFormat = "@";
eventSheet.freezePanes.freezeRows(5);

// Results formulas remain traceable to readings/configuration.
setTitle(results, "Test Result", "Thermal conformity and electrical-output occurrences are evaluated separately before the overall result is assigned.", "M");
results.getRange("A5:M5").values = [["Profile", "Fan Mode", "Repetition", "Criterion", "Observed Time (s)", "Reference Time (s)", "Max. Control (°C)", "Initial Valid", "Approved Stability", "Thermal Result", "Electrical Output Result", "Overall Result", "Rationale"]];
const maxRow = Math.max(6, 5 + samples.length);
const elapsedCol = colLetter(readingHeaders.indexOf("profile_elapsed_s"));
const maxControlCol = colLetter(readingHeaders.indexOf("hottest_control_c"));
const stableCol = colLetter(readingHeaders.indexOf("stable"));
const historicalEndpointCol = colLetter(readingHeaders.indexOf("historical_stress_endpoint"));
const progressStatusCol = colLetter(readingHeaders.indexOf("progress_status"));
const errorLatchedCol = colLetter(readingHeaders.indexOf("error_latched"));
const errorCodeCol = colLetter(readingHeaders.indexOf("error_code"));
results.getRange("A6:D6").values = [[profile.label || profileKey, session.fan_mode === "MAX" ? "Max Power" : "Normal", session.repetition || 1, criterionDisplay]];
results.getRange("E6").formulas = [[samples.length ? `=MAX('Readings'!$${elapsedCol}$6:$${elapsedCol}$${maxRow})` : "=0"]];
if (profile.criterion === "historical_time") {
  results.getRange("F6").formulas = [[`=${configValueCell("Effective Historical Time Reference")}`]];
} else {
  results.getRange("F6").values = [["Not applicable"]];
}
results.getRange("G6").formulas = [[samples.length ? `=MAX('Readings'!$${maxControlCol}$6:$${maxControlCol}$${maxRow})` : "=0"]];
results.getRange("H6").values = [[evaluation.initial_valid ? 1 : 0]];
results.getRange("I6").formulas = [[samples.length ? `=MAX('Readings'!$${stableCol}$6:$${stableCol}$${maxRow})` : "=0"]];
const hasThermal = samples.length && configuration.event_rules?.thermal_error_always_stops_timer
  ? `COUNTIFS('Readings'!$${errorLatchedCol}$6:$${errorLatchedCol}$${maxRow},1,'Readings'!$${errorCodeCol}$6:$${errorCodeCol}$${maxRow},6)>0`
  : "FALSE";
const endpointEnabled = configuration.event_rules?.status_14_is_historical_stress_endpoint !== false
  && ["historical_time", "stable_temperature"].includes(profile.criterion);
const hasHistoricalStressEndpoint = samples.length && endpointEnabled
  ? `MAX('Readings'!$${historicalEndpointCol}$6:$${historicalEndpointCol}$${maxRow})=1`
  : "FALSE";
const hasIa1Other = samples.length ? `AND(COUNTIF('Readings'!$${progressStatusCol}$6:$${progressStatusCol}$${maxRow},14)>0,NOT(${hasThermal}))` : "FALSE";
results.getRange("J6").formulas = [[
  `=IF(D6="Monitor only — no automatic acceptance","MONITORING",IF(B6<>"Max Power","INCONCLUSIVE",IF(H6=0,"INCONCLUSIVE",IF(D6="Historical time reference",IF(E6>=F6,"PASS",IF(${hasThermal},"FAIL","INCONCLUSIVE")),` +
  `IF(D6="Stable temperature limits",IF(G6>${configValueCell("Review Up To")},"FAIL",IF(I6=0,IF(${hasThermal},"FAIL","INCONCLUSIVE"),IF(G6<=${configValueCell("Pass Up To")},"PASS","REVIEW"))),` +
  `IF(D6="Reach stability without Thermal Error",IF(${hasThermal},"FAIL",IF(I6=1,"PASS","INCONCLUSIVE")),"INCONCLUSIVE"))))))`
]];
results.getRange("K6").formulas = [[`=IF(${hasIa1Other},"REVIEW","NO ERROR OBSERVED")`]];
results.getRange("L6").formulas = [[`=IF(J6="FAIL","FAIL",IF(K6="REVIEW","REVIEW",J6))`]];
results.getRange("M6").values = [[evaluation.reason || ""]];
results.getRange("E6:I6").format.numberFormat = "0.000";
styleTable(results, results.getRange("A5:M6"), results.getRange("A5:M5"));
results.getRange("A5:M6").format.wrapText = true;
const resultWidths = [34, 12, 11, 24, 16, 16, 16, 12, 17, 14, 20, 14, 58];
resultWidths.forEach((width, index) => results.getRangeByIndexes(4, index, 2, 1).format.columnWidth = width);
results.getRange("A5:M6").format.autofitRows();
results.getRange("A6:M6").format.rowHeight = 68;
for (const address of ["J6", "K6", "L6"]) {
  results.getRange(address).conditionalFormats.add("containsText", { text: "PASS", format: { fill: paleGreen, font: { color: "#1E6A42", bold: true } } });
  results.getRange(address).conditionalFormats.add("containsText", { text: "NO ERROR OBSERVED", format: { fill: paleGreen, font: { color: "#1E6A42", bold: true } } });
  results.getRange(address).conditionalFormats.add("containsText", { text: "REVIEW", format: { fill: paleAmber, font: { color: "#8A5A00", bold: true } } });
  results.getRange(address).conditionalFormats.add("containsText", { text: "FAIL", format: { fill: paleRed, font: { color: "#A12622", bold: true } } });
  results.getRange(address).conditionalFormats.add("containsText", { text: "INCONCLUSIVE", format: { fill: light, font: { color: gray, bold: true } } });
  results.getRange(address).conditionalFormats.add("containsText", { text: "MONITORING", format: { fill: paleBlue, font: { color: blue, bold: true } } });
}
results.getRange("A9:F9").merge();
results.getRange("A9").values = [["Range-only Stability Diagnostics — Approved Rule Also Requires the Trend Limit"]];
results.getRange("A9:F9").format = { fill: paleAmber, font: { bold: true, color: "#8A5A00" } };
results.getRange("A10:F10").values = [["Window (s)", "Range Limit (°C)", "Observed Duration (s)", "Minimum Range (°C)", "Condition Met", "Acceptance Effect"]];
const diagnosticRows = stabilityDiagnostics.map(item => [
  item.window_s ?? null,
  item.range_limit_c ?? null,
  item.observed_duration_s ?? null,
  item.minimum_range_c ?? null,
  item.met ? "Yes" : "No",
  "None — diagnostic only",
]);
if (diagnosticRows.length) results.getRangeByIndexes(10, 0, diagnosticRows.length, 6).values = diagnosticRows;
const diagnosticLastRow = Math.max(11, 10 + diagnosticRows.length);
styleTable(results, results.getRange(`A10:F${diagnosticLastRow}`), results.getRange("A10:F10"));
results.getRange(`A11:D${diagnosticLastRow}`).format.numberFormat = "0.000";
results.getRange(`F11:F${diagnosticLastRow}`).format.wrapText = true;
const diagnosticWidths = [14, 18, 21, 20, 15, 24];
diagnosticWidths.forEach((width, index) => results.getRangeByIndexes(9, index, 1 + diagnosticRows.length, 1).format.columnWidth = width);
results.getRange(`A10:F${diagnosticLastRow}`).format.autofitRows();
results.getRange("A16:M16").merge();
results.getRange("A16").values = [["Signatures"]];
results.getRange("A16:M16").format = { fill: navy, font: { bold: true, color: white } };
results.getRange("A18:D18").merge(); results.getRange("A18").values = [["Performed by: ______________________________"]];
results.getRange("F18:M18").merge(); results.getRange("F18").values = [["Date: ____/____/________"]];
results.getRange("A20:D20").merge(); results.getRange("A20").values = [["Reviewed by: ______________________________"]];
results.getRange("F20:M20").merge(); results.getRange("F20").values = [["Date: ____/____/________"]];

// Summary dashboard.
setTitle(summary, "AMT205 Thermal Validation Report", "Consolidated report generated by the passive monitor. See Traceability for origin and limitations.", "H");
summary.getRange("A5:B13").values = [
  ["Equipment", session.model || configuration.equipment?.model || "Vebko AMT205 V8"],
  ["Firmware", session.firmware || configuration.equipment?.firmware || ""],
  ["Serial Number", session.serial_number || "Not provided"],
  ["Profile", profile.label || profileKey],
  ["Fan Mode", session.fan_mode === "MAX" ? "Max Power" : "Normal"],
  ["Thermal Recovery", recovery.enabled === false ? "Disabled" : recovery.reached ? `${Number(recovery.time_to_target_s).toFixed(1)} s to ${Number(recovery.target_c).toFixed(1)} °C target` : recovery.timed_out ? "Target not reached" : "Not completed"],
  ["Initial Baseline", baseline.confirmed ? `${Number(baseline.temperature_c).toFixed(1)} °C — ${baseline.sensor || "sensor not recorded"}` : "Not confirmed"],
  ["Baseline Evidence", baseline.confirmed ? `${Number(baseline.observation_s).toFixed(0)} s · range ${Number(baseline.range_c).toFixed(1)} °C · trend ${Number(baseline.slope_c_per_min).toFixed(3)} °C/min` : "Not available"],
  ["Max. Control", null],
];
summary.getRange("B13").formulas = [[`='Results'!G6`]];
summary.getRange("B13").format.numberFormat = "0.0 °C";
summary.getRange("A5:A13").format = { fill: paleBlue, font: { bold: true, color: navy } };
summary.getRange("B5:B13").format.wrapText = true;
summary.getRange("A5:B13").format.borders = { preset: "outside", style: "thin", color: "#B8C7D1" };
summary.getRange("A5:B13").format.autofitRows();
summary.getRange("D5:H5").merge(); summary.getRange("D5").values = [["RESULT"]];
summary.getRange("D5:H5").format = { fill: navy, font: { bold: true, color: white }, horizontalAlignment: "center" };
summary.getRange("D6:H8").merge(); summary.getRange("D6").formulas = [["='Results'!L6"]];
summary.getRange("D6:H8").format = { fill: light, font: { bold: true, color: navy, size: 24 }, horizontalAlignment: "center", verticalAlignment: "center" };
summary.getRange("D9:E10").merge(); summary.getRange("D9").values = [["Thermal Result"]];
summary.getRange("F9:H10").merge(); summary.getRange("F9").formulas = [["='Results'!J6"]];
summary.getRange("D9:E10").format = { fill: paleBlue, font: { bold: true, color: navy }, horizontalAlignment: "center", verticalAlignment: "center", wrapText: true };
summary.getRange("F9:H10").format = { fill: paleGreen, font: { bold: true, color: teal, size: 16 }, horizontalAlignment: "center", verticalAlignment: "center" };
summary.getRange("D11:E12").merge(); summary.getRange("D11").values = [["Electrical Output Result"]];
summary.getRange("F11:H12").merge(); summary.getRange("F11").formulas = [["='Results'!K6"]];
summary.getRange("D11:E12").format = { fill: paleBlue, font: { bold: true, color: navy }, horizontalAlignment: "center", verticalAlignment: "center", wrapText: true };
summary.getRange("F11:H12").format = { fill: paleAmber, font: { bold: true, color: "#8A5A00", size: 14 }, horizontalAlignment: "center", verticalAlignment: "center", wrapText: true };
summary.getRange("A15:H15").merge(); summary.getRange("A15").values = [["Automatic Conclusion"]];
summary.getRange("A15:H15").format = { fill: blue, font: { bold: true, color: white } };
summary.getRange("A16:H18").merge(); summary.getRange("A16").formulas = [["='Results'!M6"]];
summary.getRange("A16:H18").format = { fill: light, wrapText: true, verticalAlignment: "top" };
summary.getRange("A21:H21").merge(); summary.getRange("A21").values = [["Operator Notes"]];
summary.getRange("A21:H21").format = { fill: blue, font: { bold: true, color: white } };
summary.getRange("A22:H25").merge(); summary.getRange("A22").values = [[session.notes || "Free field for observations and conditions that were not captured automatically."]];
summary.getRange("A22:H25").format = { fill: "#FFFDF5", wrapText: true, verticalAlignment: "top", borders: { preset: "outside", style: "thin", color: "#D7C99A" } };
summary.getRange("A1:H25").format.columnWidth = 16;
summary.getRange("B5:B10").format.columnWidth = 34;

// Charts sheet uses formula-backed helper data.
setTitle(charts, "Thermal Trend", "The chart highlights the hottest V/I sensor (control) and the hottest mapped internal sensor.", "N");
charts.getRange("A5:C5").values = [["Relative Time (s)", "Max. V/I (°C)", "Max. Internal (°C)"]];
if (samples.length) {
  const relativeCol = colLetter(readingHeaders.indexOf("relative_s"));
  const hottestInternalCol = colLetter(readingHeaders.indexOf("hottest_internal_c"));
  const step = Math.max(1, Math.ceil(samples.length / 20));
  const chartIndexes = [];
  for (let index = 0; index < samples.length; index += step) chartIndexes.push(index);
  if (chartIndexes.at(-1) !== samples.length - 1) chartIndexes.push(samples.length - 1);
  const helperFormulas = chartIndexes.map(index => [
    `='Readings'!${relativeCol}${6 + index}`,
    `='Readings'!${maxControlCol}${6 + index}`,
    `='Readings'!${hottestInternalCol}${6 + index}`,
  ]);
  charts.getRangeByIndexes(5, 0, helperFormulas.length, 3).formulas = helperFormulas;
  const chartLastRow = 5 + helperFormulas.length;
  charts.getRange(`A5:C${chartLastRow}`).format.numberFormat = "0.0";
  const chart = charts.charts.add("line", charts.getRange(`A5:C${chartLastRow}`));
  chart.title = "Thermal Trend During Test (°C)";
  chart.hasLegend = true;
  chart.xAxis = { axisType: "textAxis", textStyle: { fontSize: 9 } };
  chart.yAxis = { numberFormatCode: "0.0", title: { text: "Temperature (°C)" } };
  chart.setPosition("E5", "N24");
}
styleTable(charts, charts.getRange(`A5:C${Math.max(6, 5 + Math.min(samples.length, 41))}`), charts.getRange("A5:C5"));
charts.freezePanes.freezeRows(5);

// Traceability sheet.
setTitle(trace, "Traceability and Limitations", "Evidence identification, interpretation versions and known limitations.", "H");
const limitations = metadata.limitations || [];
const traceRows = [
  ["Field", "Value"],
  ["Application Version", metadata.application_version || ""],
  ["Protocol Map Version", metadata.protocol_map_version || ""],
  ["Generated At", localTimestampText(metadata.generated_at || "")],
  ["Source File", metadata.capture?.source || ""],
  ["Source File SHA-256", metadata.capture?.sha256 || ""],
  ["Reading Count", metadata.capture?.sample_count ?? 0],
  ["Event Count", metadata.capture?.event_count ?? 0],
  ["First Reading", localTimestampText(metadata.capture?.first_timestamp || "")],
  ["Last Reading", localTimestampText(metadata.capture?.last_timestamp || "")],
  ["Model", session.model || configuration.equipment?.model || ""],
  ["Firmware", session.firmware || configuration.equipment?.firmware || ""],
  ["Supply", session.supply || configuration.equipment?.supply || ""],
  ["Grounding", session.grounding || configuration.equipment?.grounding || ""],
  ["Protections", session.protections || configuration.equipment?.protections || ""],
  ["Ambient Temperature", session.ambient_c ?? ""],
  ["Smart Delay (Turn On/Off Fan)", session.smart_delay_enabled == null ? "Not provided" : session.smart_delay_enabled ? "Enabled" : "Disabled"],
  ["Smart Delay Min.", session.smart_delay_min_c ?? ""],
  ["Smart Delay Max.", session.smart_delay_max_c ?? ""],
  ["Max. Value", session.maximum_temperature_setting_c ?? ""],
  ["Earth — Enable", session.earth_enabled == null ? "Not provided" : session.earth_enabled ? "Enabled" : "Disabled"],
  ["Fan Mode (Max Power)", session.fan_mode === "MAX" ? "Enabled" : "Disabled"],
  ["Setpoint vs. Internal Feedback", session.compare_feedback ? "Recommended / recorded" : "Not recorded"],
  ["Initial Workflow Confirmed", metadata.workflow?.initial_confirmed ? "Yes" : "No"],
  ["Initial Baseline Temperature", baseline.temperature_c ?? "Not confirmed"],
  ["Initial Baseline Sensor", baseline.sensor || "Not confirmed"],
  ["Initial Baseline Observation", baseline.observation_s ?? "Not available"],
  ["Initial Baseline Range", baseline.range_c ?? "Not available"],
  ["Initial Baseline Trend", baseline.slope_c_per_min ?? "Not available"],
  ["Recovery Target", recovery.target_c ?? "Not recorded"],
  ["Recovery Tolerance", recovery.tolerance_c ?? "Not recorded"],
  ["Recovery Hold", recovery.hold_s ?? "Not recorded"],
  ["Recovery Start Temperature", recovery.start_temperature_c ?? "Not recorded"],
  ["Recovery End Temperature", recovery.end_temperature_c ?? "Not recorded"],
  ["Recovery Time to Target", recovery.time_to_target_s ?? "Not reached"],
  ["Recovery Result", recovery.reached ? "Reached" : recovery.timed_out ? "Not reached within maximum duration" : "Not completed"],
  ["Stability Window Approved", stabilityApproved ? "Yes" : "No"],
  ["Diagnostic Stability Windows", stabilityDiagnostics.map(item => `${item.window_s} s`).join(", ") || "Not recorded"],
  ["Injection Observed", metadata.workflow?.injection_seen ? "Yes" : "No"],
  ["IA1 Other Electrical Endpoint", metadata.workflow?.historical_stress_endpoint_observed ? "Yes" : "No"],
  ["Final Workflow Step", metadata.workflow?.final_step?.code || "Not available"],
];
trace.getRange(`A5:B${4 + traceRows.length}`).values = traceRows;
styleTable(trace, trace.getRange(`A5:B${4 + traceRows.length}`), trace.getRange("A5:B5"));
let limitationRow = 6 + traceRows.length;
trace.getRange(`A${limitationRow}:H${limitationRow}`).merge(); trace.getRange(`A${limitationRow}`).values = [["Known Limitations"]];
trace.getRange(`A${limitationRow}:H${limitationRow}`).format = { fill: navy, font: { bold: true, color: white } };
limitations.forEach((item, index) => {
  const row = limitationRow + 1 + index;
  trace.getRange(`A${row}:H${row}`).merge(); trace.getRange(`A${row}`).values = [[`• ${item}`]];
  trace.getRange(`A${row}:H${row}`).format = { fill: index % 2 ? white : light, wrapText: true };
});
trace.getRange(`A1:A${limitationRow + limitations.length}`).format.columnWidth = 30;
trace.getRange(`B1:B${limitationRow + limitations.length}`).format.columnWidth = 68;
trace.getRange("B8").format.numberFormat = "@";
trace.getRange("B13:B14").format.numberFormat = "@";
trace.getRange("B29").format.numberFormat = "0.0";
trace.getRange("B31:B32").format.numberFormat = "0.0";
trace.getRange("B33").format.numberFormat = "0.000";
configSheet.getRange(`A1:A${4 + configRows.length}`).format.columnWidth = 43;
configSheet.getRange(`B1:B${4 + configRows.length}`).format.columnWidth = 40;

// Formula/error inspection precedes export.
const formulaCheck = await workbook.inspect({ kind: "formula", sheetId: "Results", range: "A1:M20", maxChars: 4000, options: { maxResults: 50 } });
const formulaText = formulaCheck?.ndjson || "";
if (!formulaText) throw new Error("The report formulas could not be inspected.");
if (formulaText.includes("#REF!") || formulaText.includes("#NAME?")) throw new Error("The report contains an invalid formula.");
const errorCheck = await workbook.inspect({ kind: "match", searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A", options: { useRegex: true, maxResults: 100 }, maxChars: 4000 });
const formulaErrors = (errorCheck?.ndjson || "").split(/\r?\n/).filter(Boolean).some(line => {
  try { return JSON.parse(line).kind === "match"; } catch { return false; }
});
if (formulaErrors) throw new Error("The report contains a formula error.");

await fs.mkdir(path.dirname(outputPath), { recursive: true });
const output = await SpreadsheetFile.exportXlsx(workbook);
await output.save(outputPath);
if (previewDir) {
  await fs.mkdir(previewDir, { recursive: true });
  const previewRanges = {
    // Summary contains several merged, cross-sheet formula panels. The native
    // renderer loses those panels when a fixed range is supplied; auto-crop
    // preserves the rendered dashboard while keeping the exported workbook
    // unchanged.
    Summary: null,
    Results: "A1:M20",
    Readings: `A1:${colLetter(Math.max(2, readingHeaders.length - 1))}${Math.min(45, 5 + samples.length)}`,
    Events: `A1:${colLetter(Math.max(7, eventHeaders.length - 1))}${Math.min(45, 5 + events.length)}`,
    Charts: "A1:N24",
    Configuration: `A1:D${4 + configRows.length}`,
    Traceability: `A1:H${limitationRow + limitations.length}`,
  };
  for (const [sheetName, range] of Object.entries(previewRanges)) {
    try {
      const preview = await workbook.render(range ? { sheetName, range, scale: 1, format: "png" } : { sheetName, autoCrop: "all", scale: 1, format: "png" });
      await fs.writeFile(path.join(previewDir, `${sheetName}.png`), new Uint8Array(await preview.arrayBuffer()));
    } catch (error) {
      console.warn(`Preview skipped for ${sheetName}: ${error instanceof Error ? error.message : String(error)}`);
    }
  }
}
console.log(outputPath);
// The bundled renderer can raise a native Windows shutdown fault after all files
// have been flushed. Exiting explicitly here keeps the operator workflow clean.
process.exit(0);

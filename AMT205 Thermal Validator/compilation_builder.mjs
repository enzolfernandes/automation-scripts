import fs from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";

const [inputPath, outputPath, previewPath] = process.argv.slice(2);
if (!inputPath || !outputPath || !process.env.ARTIFACT_TOOL_ENTRY) {
  throw new Error("Usage: compilation_builder.mjs <input.json> <output.xlsx> <comparison.png>");
}

const { SpreadsheetFile, Workbook } = await import(pathToFileURL(process.env.ARTIFACT_TOOL_ENTRY).href);
const payload = JSON.parse(await fs.readFile(inputPath, "utf8"));
const sessions = payload.sessions || [];
const chartTimes = payload.chart?.times || [];
const chartSeries = payload.chart?.series || [];
const readings = payload.readings || [];
if (!sessions.length || !chartTimes.length || !chartSeries.length) {
  throw new Error("The selected folders do not contain usable AMT205 session data.");
}

const workbook = Workbook.create();
const summary = workbook.worksheets.add("Summary");
const comparison = workbook.worksheets.add("Comparison");
const readingSheet = workbook.worksheets.add("Compiled Readings");

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

function setTitle(sheet, title, subtitle, endCol) {
  sheet.showGridLines = false;
  sheet.getRange(`A1:${endCol}2`).merge();
  sheet.getRange("A1").values = [[title]];
  sheet.getRange(`A1:${endCol}2`).format = {
    fill: navy,
    font: { bold: true, color: white, size: 18 },
    verticalAlignment: "center",
    horizontalAlignment: "left",
  };
  sheet.getRange(`A3:${endCol}3`).merge();
  sheet.getRange("A3").values = [[subtitle]];
  sheet.getRange(`A3:${endCol}3`).format = { fill: paleBlue, font: { color: gray, italic: true }, wrapText: true };
  sheet.getRange("A1:A3").format.rowHeight = 25;
}

function styleTable(range, headerRange) {
  headerRange.format = { fill: blue, font: { bold: true, color: white }, wrapText: true, verticalAlignment: "center" };
  range.format.borders = { preset: "inside", style: "thin", color: "#D9E1E6" };
  range.format.autofitRows();
}

function localTimestampText(value) {
  if (!value) return "";
  return String(value).replace("T", " ").replace(/(?:Z|[+-]\d{2}:\d{2})$/, "");
}

setTitle(summary, "AMT205 Thermal Session Compilation", `${sessions.length} successive cycle(s) compiled from ${payload.source_root || "the selected folder"}. Historical-time acceptance applies only to Max Power; Normal mode remains comparative.`, "T");
const summaryHeaders = [
  "Cycle", "Session", "Date / Time", "Interval Since Previous (s)", "Serial Number", "Profile", "Rep.", "Fan Mode", "Recorded Frequency (Hz)",
  "Initial (°C)", "Reference (s)", "Observed (s)", "Margin (%)", "Max. V/I (°C)", "Recovery Target (°C)", "Recovery Time (s)", "Recovery End (°C)", "Recovery Result", "Quantitative Result", "Recorded Result",
];
const summaryRows = sessions.map(item => [
  item.cycle_number,
  item.session_name,
  localTimestampText(item.timestamp),
  item.interval_since_previous_s,
  item.serial_number,
  item.profile_short,
  item.repetition,
  item.fan_mode,
  item.frequency_hz,
  item.initial_c,
  item.reference_s,
  item.observed_s,
  item.margin_percent,
  item.max_control_c,
  item.recovery_target_c,
  item.recovery_time_s,
  item.recovery_end_c,
  item.recovery_result,
  item.quantitative_result,
  item.overall_result,
]);
summary.getRangeByIndexes(4, 0, 1 + summaryRows.length, summaryHeaders.length).values = [summaryHeaders, ...summaryRows];
const summaryRange = summary.getRangeByIndexes(4, 0, 1 + summaryRows.length, summaryHeaders.length);
styleTable(summaryRange, summary.getRangeByIndexes(4, 0, 1, summaryHeaders.length));
summary.tables.add(`A5:T${5 + summaryRows.length}`, true, "SessionCompilationTable");
summary.getRange(`C6:C${5 + summaryRows.length}`).format.numberFormat = "@";
summary.getRange(`D6:Q${5 + summaryRows.length}`).format.numberFormat = "0.000";
summary.getRange(`M6:M${5 + summaryRows.length}`).format.numberFormat = "+0.0;-0.0;0.0";
const summaryWidths = [8, 25, 23, 20, 17, 20, 8, 19, 19, 13, 15, 15, 13, 15, 18, 18, 18, 18, 20, 18];
summaryWidths.forEach((width, index) => summary.getRangeByIndexes(4, index, 1 + summaryRows.length, 1).format.columnWidth = width);
summary.getRange(`A5:T${5 + summaryRows.length}`).format.wrapText = true;
summary.getRange("A5:T5").format.rowHeight = 38;
summary.freezePanes.freezeRows(5);
for (const address of [`R6:T${5 + summaryRows.length}`]) {
  summary.getRange(address).conditionalFormats.add("containsText", { text: "PASS", format: { fill: paleGreen, font: { color: teal, bold: true } } });
  summary.getRange(address).conditionalFormats.add("containsText", { text: "REVIEW", format: { fill: paleAmber, font: { color: "#8A5A00", bold: true } } });
  summary.getRange(address).conditionalFormats.add("containsText", { text: "FAIL", format: { fill: paleRed, font: { color: "#A12622", bold: true } } });
  summary.getRange(address).conditionalFormats.add("containsText", { text: "INCONCLUSIVE", format: { fill: light, font: { color: gray, bold: true } } });
}

setTitle(comparison, "Thermal Comparison", "Hottest V/I sensor during injection. Curves use a common time scale; blank tails mean that the corresponding injection had already ended.", "J");
const chartHeaders = ["Injection Time (s)", ...chartSeries.map(item => item.label)];
const chartRows = chartTimes.map((elapsed, rowIndex) => [elapsed, ...chartSeries.map(item => item.values[rowIndex] ?? null)]);
const chartStartColumn = 11; // L; keeps helper data to the right of the rendered chart.
comparison.getRangeByIndexes(4, chartStartColumn, 1 + chartRows.length, chartHeaders.length).values = [chartHeaders, ...chartRows];
const chartDataRange = comparison.getRangeByIndexes(4, chartStartColumn, 1 + chartRows.length, chartHeaders.length);
styleTable(chartDataRange, comparison.getRangeByIndexes(4, chartStartColumn, 1, chartHeaders.length));
chartDataRange.format.numberFormat = "0.0";
chartDataRange.format.columnWidth = 18;
comparison.freezePanes.freezeRows(5);
const chart = comparison.charts.add("line", chartDataRange);
chart.title = "Hottest V/I Temperature by Session";
chart.hasLegend = true;
chart.xAxis = { axisType: "textAxis", title: { text: "Injection Time (s)" }, textStyle: { fontSize: 9 } };
chart.yAxis = { numberFormatCode: "0.0", title: { text: "Temperature (°C)" } };
chart.setPosition("A5", "J28");

setTitle(readingSheet, "Compiled Injection Readings", "Long-form data used by the comparison chart. Each row remains associated with its original session folder.", "F");
const readingHeaders = ["Session", "Session Folder", "Injection Time (s)", "Hottest V/I (°C)", "Hottest Internal (°C)"];
const readingRows = readings.map(item => [
  item.session,
  item.session_name,
  item.injection_time_s,
  item.hottest_control_c,
  item.hottest_internal_c,
]);
readingSheet.getRangeByIndexes(4, 0, 1 + readingRows.length, readingHeaders.length).values = [readingHeaders, ...readingRows];
const readingRange = readingSheet.getRangeByIndexes(4, 0, 1 + readingRows.length, readingHeaders.length);
styleTable(readingRange, readingSheet.getRangeByIndexes(4, 0, 1, readingHeaders.length));
readingSheet.tables.add(`A5:E${5 + readingRows.length}`, true, "CompiledReadingsTable");
readingSheet.getRange(`C6:E${5 + readingRows.length}`).format.numberFormat = "0.000";
[25, 25, 20, 20, 22].forEach((width, index) => readingSheet.getRangeByIndexes(4, index, 1 + readingRows.length, 1).format.columnWidth = width);
readingSheet.freezePanes.freezeRows(5);

const errorCheck = await workbook.inspect({ kind: "match", searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A", options: { useRegex: true, maxResults: 100 }, maxChars: 4000 });
const hasErrors = (errorCheck?.ndjson || "").split(/\r?\n/).filter(Boolean).some(line => {
  try { return JSON.parse(line).kind === "match"; } catch { return false; }
});
if (hasErrors) throw new Error("The consolidated workbook contains a formula error.");

await fs.mkdir(path.dirname(outputPath), { recursive: true });
const exported = await SpreadsheetFile.exportXlsx(workbook);
await exported.save(outputPath);
if (previewPath) {
  const preview = await workbook.render({ sheetName: "Comparison", range: "A1:J30", scale: 1.25, format: "png" });
  await fs.writeFile(previewPath, new Uint8Array(await preview.arrayBuffer()));
}
console.log(outputPath);
process.exit(0);

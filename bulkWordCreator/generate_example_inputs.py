from pathlib import Path
from datetime import datetime
from openpyxl import Workbook
from docx import Document

base = Path(__file__).resolve().parent
workbook = Workbook()
sheet = workbook.active
sheet.title = "Planilha1"
sheet.append(["OS", "modelo", "numeroDeSerie", "firmware", "dataDeFabricacao", "dataDoTeste"])
sheet.append(["DEMO-001", "EXAMPLE-RELAY", "DEMO-SERIAL-001", "EXAMPLE", datetime(2020, 1, 1), datetime(2026, 1, 1)])
workbook.save(base / "OS_Database.xlsx")
document = Document()
document.add_heading("EXAMPLE ONLY - Test report", 0)
document.add_paragraph("Synthetic data. Not a certificate or an actual equipment test result.")
for label, field in [("Service order", "OS"), ("Model", "modelo"), ("Serial number", "numeroDeSerie"), ("Firmware", "firmware"), ("Manufacturing date", "dataDeFabricacao"), ("Test date", "dataDoTeste")]:
    document.add_paragraph(label + ": {{ " + field + " }}")
document.add_paragraph("Record actual test methods, measurements, acceptance criteria and conclusions before issuing a real report.")
document.save(base / "OS_Modelo.docx")
print("Created synthetic example inputs in", base)

from pathlib import Path

import pandas as pd  # pip install pandas openpyxl
from docxtpl import DocxTemplate  # pip install docxtpl

base_dir = Path(__file__).parent if "__file__" in locals() else Path.cwd()
word_template_path = base_dir / "OS_Modelo.docx"
excel_path = base_dir / "OS_Database.xlsx"
output_dir = base_dir / "OUTPUT"

# Create output folder for the word documents
output_dir.mkdir(exist_ok=True)

# Convert Excel sheet to pandas dataframe
df = pd.read_excel(excel_path, sheet_name="Planilha1")

# Keep only date part YYYY-MM-DD (not the time)
df["dataDeFabricacao"] = pd.to_datetime(df["dataDeFabricacao"]).dt.strftime("%d/%m/%Y")
df["dataDoTeste"] = pd.to_datetime(df["dataDoTeste"]).dt.strftime("%d/%m/%Y")

# Iterate over each row in df and render word document
for record in df.to_dict(orient="records"):
    doc = DocxTemplate(word_template_path)
    doc.render(record)
    output_path = output_dir / f"Relatório de Teste {record['OS']}_{record['modelo']}.docx"
    doc.save(output_path)

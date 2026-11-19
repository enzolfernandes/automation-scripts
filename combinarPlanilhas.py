import pandas as pd
import os

# Defina a pasta onde estão os arquivos
pasta = "Listas de Capacitores - Digikey"

# Lista todos os arquivos .xlsx na pasta
arquivos = [f for f in os.listdir(pasta) if f.endswith(".xlsx")]

# Cria um escritor de arquivos Excel
with pd.ExcelWriter("Planilha_Unificada.xlsx", engine="openpyxl") as writer:
    for arquivo in arquivos:
        caminho_arquivo = os.path.join(pasta, arquivo)
        df = pd.read_excel(caminho_arquivo)  # Lê a planilha
        nome_aba = os.path.splitext(arquivo)[0]  # Usa o nome do arquivo como nome da aba
        df.to_excel(writer, sheet_name=nome_aba, index=False)  # Salva na aba correspondente

print("Planilhas combinadas com sucesso!")

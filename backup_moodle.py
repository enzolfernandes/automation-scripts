import os
import requests
from bs4 import BeautifulSoup
from urllib.parse import unquote

# --- CONFIGURAÇÕES ---
URL_CURSO = "https://moodle.ufabc.edu.br/course/view.php?id=759"
COOKIES = {'MoodleSession': os.environ.get('MOODLE_SESSION', '')}
if not COOKIES['MoodleSession']:
    raise RuntimeError('Set MOODLE_SESSION locally before running this script.')

# --- OPÇÕES ---
# Mude para True se quiser que ele baixe os arquivos para seu computador.
# Mude para False se quiser apenas gerar o texto com os links originais.
BAIXAR_ARQUIVOS_LOCALMENTE = False 

# Define a pasta onde o script está como local de salvamento
OUTPUT_FOLDER = os.path.dirname(os.path.abspath(__file__))
ARQUIVO_TEXTO = "conteudo_curso_links.md"

def limpar_nome(nome):
    """Remove caracteres inválidos para nome de arquivo."""
    return "".join([c for c in nome if c.isalnum() or c in (' ', '-', '_', '.')]).strip()

def limpar_texto_moodle(texto):
    """Remove textos de sistema do Moodle."""
    if not texto: return ""
    termos_inuteis = [
        "Select activity", "Mark as done", "Opened:", "Due:", "Receive a grade",
        "To do:", "Done:", "Failed:", "View:", "Make a submission"
    ]
    for termo in termos_inuteis:
        texto = texto.replace(termo, "")
    return texto.strip()

def baixar_arquivo(url, nome_exibicao, pasta_destino):
    """Baixa o arquivo silenciosamente se a opção estiver ativada."""
    try:
        print(f"   -> Baixando: {nome_exibicao}...")
        response = requests.get(url, cookies=COOKIES, allow_redirects=True)
        
        filename = f"{limpar_nome(nome_exibicao)}.pdf"
        if "Content-Disposition" in response.headers:
            cd = response.headers["Content-Disposition"]
            if "filename=" in cd:
                filename = cd.split("filename=")[1].strip('"')
                filename = unquote(filename)

        caminho_completo = os.path.join(pasta_destino, filename)
        with open(caminho_completo, 'wb') as f:
            f.write(response.content)
        return filename
    except Exception as e:
        print(f"   [ERRO] Falha ao baixar {nome_exibicao}: {e}")
        return None

def extrair_texto_elemento(elemento):
    """Extrai texto preservando quebras de linha."""
    if not elemento: return ""
    for br in elemento.find_all("br"):
        br.replace_with("\n")
    for p in elemento.find_all("p"):
        p.append("\n\n")
    return elemento.get_text(strip=True)

def extrair_curso():
    print(f"Modo de Download: {'ATIVADO' if BAIXAR_ARQUIVOS_LOCALMENTE else 'DESATIVADO'}")
    print(f"Processando curso: {URL_CURSO}")
    
    response = requests.get(URL_CURSO, cookies=COOKIES)
    
    if response.status_code != 200:
        print(f"Erro de conexão. Status: {response.status_code}")
        return

    soup = BeautifulSoup(response.content, 'html.parser')
    
    caminho_texto = os.path.join(OUTPUT_FOLDER, ARQUIVO_TEXTO)
    with open(caminho_texto, 'w', encoding='utf-8') as f_txt:
        
        # Título do Curso
        titulo_curso = soup.find('h1') or soup.find('div', class_='page-header-headings')
        if titulo_curso:
            f_txt.write(f"# {titulo_curso.get_text(strip=True)}\n\n")

        secoes = soup.find_all('li', class_='section')
        if not secoes: secoes = soup.find_all('div', class_='section')

        for secao in secoes:
            nome_secao = secao.find('h3', class_='sectionname') or secao.find('span', class_='sectionname')
            titulo_txt = nome_secao.get_text(strip=True) if nome_secao else "Seção Geral"
            
            if 'hidden' in secao.get('class', []) or not nome_secao:
                continue

            f_txt.write(f"## {titulo_txt}\n\n")
            print(f"Lendo seção: {titulo_txt}")

            # Texto Explicativo
            elementos_texto = secao.find_all(['div'], class_=['summary', 'contentwithoutlink', 'no-overflow'])
            for elem in elementos_texto:
                # Pula se o texto pertencer a uma atividade (para não duplicar)
                if elem.find_parent(class_='activity') and 'modtype_label' not in elem.find_parent(class_='activity').get('class', []):
                    continue
                
                texto = extrair_texto_elemento(elem)
                texto_limpo = limpar_texto_moodle(texto)
                if len(texto_limpo) > 2:
                    f_txt.write(f"{texto_limpo}\n\n")

            # Atividades
            atividades = secao.find_all('li', class_='activity')
            
            for atv in atividades:
                link_tag = atv.find('a', href=True)
                if not link_tag: continue

                href = link_tag['href']
                nome_bruto = link_tag.get_text(" | ", strip=True).split("|")[0]
                nome_atividade = limpar_texto_moodle(nome_bruto)
                classes = atv.get('class', [])
                
                # Formata a linha do Markdown com o LINK ORIGINAL
                linha_markdown = ""
                
                if 'modtype_resource' in classes or 'modtype_folder' in classes:
                    linha_markdown = f"- 📄 **[ARQUIVO] [{nome_atividade}]({href})**"
                    # Lógica opcional de download
                    if BAIXAR_ARQUIVOS_LOCALMENTE:
                        nome_salvo = baixar_arquivo(href, nome_atividade, OUTPUT_FOLDER)
                        if nome_salvo:
                            linha_markdown += f" *(Salvo localmente)*"
                
                elif 'modtype_url' in classes:
                    linha_markdown = f"- 🔗 **[LINK] [{nome_atividade}]({href})**"
                
                elif 'modtype_assign' in classes:
                    linha_markdown = f"- 📝 **[TAREFA] [{nome_atividade}]({href})**"
                
                elif 'modtype_forum' in classes:
                    linha_markdown = f"- 💬 **[FÓRUM] [{nome_atividade}]({href})**"
                    
                elif 'modtype_page' in classes:
                    linha_markdown = f"- 📄 **[PÁGINA] [{nome_atividade}]({href})**"

                elif 'modtype_quiz' in classes:
                     linha_markdown = f"- ❓ **[QUESTIONÁRIO] [{nome_atividade}]({href})**"

                if linha_markdown:
                    f_txt.write(f"{linha_markdown}\n")

            f_txt.write("\n---\n\n")

    print(f"\nConcluído! Arquivo gerado: {ARQUIVO_TEXTO}")

if __name__ == "__main__":
    extrair_curso()
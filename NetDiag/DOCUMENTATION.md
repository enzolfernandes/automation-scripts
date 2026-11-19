# NetDiag — Documentação Técnica

**Ferramenta:** TCP/ICMP Industrial Diagnostic Suite  
**Versão:** V5 (refatorada)  
**Autor:** Enzo Fernandes  
**GitHub:** https://github.com/enzolfernandes  

---

## Sumário

1. [Visão Geral](#1-visão-geral)
2. [Requisitos do Sistema](#2-requisitos-do-sistema)
3. [Arquitetura e Fluxo de Execução](#3-arquitetura-e-fluxo-de-execução)
4. [Parâmetros de Entrada](#4-parâmetros-de-entrada)
5. [Arquivos de Saída](#5-arquivos-de-saída)
6. [Motor de Sondagem (TcpPingRelay.ps1)](#6-motor-de-sondagem-tcppingrelayps1)
7. [Motor Heurístico de Causa Raiz](#7-motor-heurístico-de-causa-raiz)
8. [Relatório HTML e Gráfico de Latência](#8-relatório-html-e-gráfico-de-latência)
9. [Geração de PDF](#9-geração-de-pdf)
10. [Métricas Estatísticas](#10-métricas-estatísticas)
11. [Como Usar](#11-como-usar)
12. [Histórico de Melhorias Implementadas](#12-histórico-de-melhorias-implementadas)

---

## 1. Visão Geral

`tcpTestV4.bat` é uma ferramenta de diagnóstico de rede voltada a ambientes industriais e corporativos. Ela realiza **sondagem dupla** (TCP + ICMP) contra um alvo de rede por um período configurável, coletando métricas de disponibilidade e latência, e ao final produz automaticamente:

- Um log bruto em **CSV**
- Um relatório visual em **HTML** com gráfico de latência responsivo
- Um relatório final em **PDF** gerado via Microsoft Edge headless

O diferencial da ferramenta em relação a um simples `ping` ou `tracert` está em três recursos:

| Recurso | Descrição |
|---|---|
| **Sondagem TCP dupla** | Testa simultaneamente a porta de controle principal e a porta IEC 61850 MMS (padrão 102) |
| **Auditoria de hardware** | Captura automaticamente IP de origem, MAC, nome do adaptador e velocidade do link via CIM — executada antes do loop, independente de conexão |
| **Análise heurística** | Classifica automaticamente a causa raiz da falha baseando-se no padrão combinado de quedas TCP e ICMP |

---

## 2. Requisitos do Sistema

| Componente | Requisito |
|---|---|
| Sistema Operacional | Windows 10 / Windows Server 2016 ou superior |
| PowerShell | Versão 5.1 ou superior |
| Microsoft Edge | Necessário para geração de PDF (headless print); se ausente, apenas HTML é gerado |
| Permissões | Execução de scripts PowerShell com `-ExecutionPolicy RemoteSigned`; acesso à rede para o IP alvo |
| Dependências externas | Nenhuma — usa apenas APIs nativas do .NET Framework (`System.Net.Sockets`, `System.Net.NetworkInformation`) e CIM |

> **Nota:** A ferramenta **não requer instalação**. Os dois arquivos (`tcpTestV4.bat` e `TcpPingRelay.ps1`) devem estar na mesma pasta. Execute o `.bat` como usuário padrão (sem necessidade de privilégios de administrador, salvo restrições de política de execução de scripts na máquina).

---

## 3. Arquitetura e Fluxo de Execução

A ferramenta é composta por **dois arquivos permanentes** que devem residir na mesma pasta:

```
NetTest\
├── NetDiag.bat           — Orquestrador: validação de entradas + invocação do engine
└── NetDiag.Engine.ps1    — Engine: toda a lógica de sondagem, estatísticas e relatório
```

```
NetDiag.bat
│
├── [Validação]  IPv4 regex, porta numérica, duração > 0, aviso IPv6
├── [Arquivo]    Cria <pasta do script>\Diagnostic_<IP>_<timestamp>\
│
└── powershell -ExecutionPolicy RemoteSigned -File NetDiag.Engine.ps1 -Ip ... -Port1 ... ...
    │
    ├── [Startup]  Auditoria de hardware via Get-NetRoute + Get-CimInstance
    ├── LOOP (1 ciclo/s exato até timeout ou tecla Q)
    │   ├── Probe ICMP  → Ping.Send (timeout 1000ms) — captura RoundtripTime
    │   ├── Probe TCP1  → TcpClient.BeginConnect (timeout 1000ms) — captura RTT
    │   ├── Probe TCP2  → TcpClient.BeginConnect (timeout 500ms)  — captura RTT [se porta ≠ 0]
    │   ├── Atualiza contadores heurísticos inline (fwStress, phyFail)
    │   ├── Atualiza estatísticas online (algoritmo de Welford + PDV)
    │   └── Grava linha no CSV + imprime no terminal (colorido)
    │
    └── FINALLY (executado sempre, mesmo com Q)
        ├── Finaliza estatísticas (Loss, Min, Max, Avg, StdDev, PDV RFC 3393)
        ├── Heuristic Root Cause Engine
        ├── Gera HTML com gráfico Canvas responsivo (TCP + ICMP RTT)
        ├── Converte HTML → PDF (Edge headless — 3 métodos de detecção)
        ├── Limpa arquivos de log temporários do Edge
        └── Imprime tabela resumo no terminal
```

---

## 4. Parâmetros de Entrada

O usuário é solicitado interativamente em `tcpTestV4.bat`:

| # | Parâmetro | Variável BAT | Exemplo | Descrição |
|---|---|---|---|---|
| 1 | Target IP | `TARGET_IP` | `192.168.1.23` | Endereço **IPv4** do dispositivo alvo (IPv6 não suportado — exibe erro) |
| 2 | Porta primária | `TARGET_PORT` | `1001` | Porta TCP principal (protocolo de controle do dispositivo) |
| 3 | Porta secundária | `TARGET_PORT2` | `102` | Porta TCP secundária; default `102` (IEC 61850 MMS); informar `0` para ignorar |
| 4 | Duração | `RUN_TIME` | `3600` | Tempo máximo de execução em segundos (deve ser > 0) |

### Validações aplicadas pelo BAT

| Entrada | Regra | Mensagem de erro |
|---|---|---|
| IP | Detecta `:` (IPv6) antes da regex | `[ERRO] IPv6 nao e suportado.` |
| IP | Regex `^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$` | `[ERRO] Formato de IP invalido.` |
| Porta primária | `set /a` deve resultar em valor > 0 | `[ERRO] Porta primaria invalida.` |
| Duração | `set /a` deve resultar em valor > 0 | `[ERRO] Duracao invalida.` |
| PS1 Engine | `if not exist TcpPingRelay.ps1` | `[ERRO] Engine nao encontrado.` |

Em todos os casos de erro a execução retorna ao `:MENU` para nova tentativa.

**Interrupção antecipada:** pressionar `Q` ou `Escape` durante o teste encerra o loop e dispara imediatamente o bloco de relatório final.

---

## 5. Arquivos de Saída

Todos os arquivos são salvos na pasta criada automaticamente na Área de Trabalho:

```
<pasta do script>\Diagnostic_<TARGET_IP>_<DD-MM-YYYY_HH-MM-SS>\
│
├── Data_<DD-MM-YYYY_HH-MM-SS>.csv     ← Log bruto de todas as sondagens
├── Visual_Report.html                  ← Relatório interativo com gráfico dual
└── Final_Report.pdf                    ← Versão PDF do relatório HTML
```

### 5.1 Formato do CSV

O arquivo CSV usa **ponto e vírgula** como delimitador e codificação **UTF-8**:

```
DateTime;IP;Port_1;Port_2;ICMP_Status;TCP1_RTT_ms;ICMP_RTT_ms;TCP2_RTT_ms
28/05/2025 14:32:01;192.168.1.23;CONNECTED;CONNECTED;OK;4;3;6
28/05/2025 14:32:02;192.168.1.23;TIMEOUT;TIMEOUT;FAIL;N/A;N/A;N/A
28/05/2025 14:32:03;192.168.1.23;CONNECTED;TIMEOUT;OK;7;4;N/A
```

| Coluna | Valores possíveis | Descrição |
|---|---|---|
| `DateTime` | `dd/MM/yyyy HH:mm:ss` | Timestamp do ciclo |
| `IP` | IPv4 | Endereço alvo sondado |
| `Port_1` | `CONNECTED`, `TIMEOUT`, `NETWORK_FAILURE` | Status da porta primária |
| `Port_2` | `CONNECTED`, `TIMEOUT`, `FAILURE`, `SKIPPED` | Status da porta secundária |
| `ICMP_Status` | `OK`, `FAIL` | Resultado do ping ICMP |
| `TCP1_RTT_ms` | Inteiro ou `N/A` | RTT da conexão TCP na porta primária |
| `ICMP_RTT_ms` | Inteiro ou `N/A` | RTT ICMP (`RoundtripTime`) |
| `TCP2_RTT_ms` | Inteiro ou `N/A` | RTT da conexão TCP na porta secundária |

---

## 6. Motor de Sondagem (NetDiag.Engine.ps1)

O engine é um script PowerShell autônomo invocado com parâmetros nomeados pelo BAT:

```powershell
param(
    [string]$Ip,
    [int]$Port1,
    [int]$Port2,
    [int]$Duration,
    [string]$OutFile,
    [string]$HtmlFile,
    [string]$PdfFile
)
```

### 6.1 Auditoria de Hardware (startup)

Executada **uma única vez antes do loop**, usando `Find-NetRoute` para identificar exatamente qual interface o Windows utilizaria para alcançar `$Ip` — evita selecionar o adaptador Wi-Fi quando cabo e Wi-Fi estão ativos simultaneamente:

```powershell
$nr      = Find-NetRoute -RemoteIPAddress $Ip -ErrorAction Stop
$addrObj = $nr | Where-Object { $null -ne $_.IPAddress } | Select-Object -First 1
$sourceIp = if ($addrObj) { $addrObj.IPAddress } else { "N/A" }
$cimConf  = Get-CimInstance Win32_NetworkAdapterConfiguration | Where-Object { $_.IPAddress -contains $sourceIp }
$cimNet   = Get-CimInstance Win32_NetworkAdapter | Where-Object { $_.Index -eq $cimConf.Index }
```

> **Comportamento:** `Find-NetRoute` consulta a tabela de roteamento do Windows e retorna a rota mais específica para o IP alvo — não necessariamente a rota padrão (`0.0.0.0/0`). Isso garante que o adaptador identificado seja o mesmo usado pelas sondagens TCP e ICMP.

Dados coletados:

| Campo | Fonte |
|---|---|
| IP de origem | `Find-NetRoute` → `MSFT_NetIPAddress.IPAddress` |
| Nome do adaptador | `Win32_NetworkAdapterConfiguration.Description` |
| Endereço MAC | `Win32_NetworkAdapterConfiguration.MACAddress` |
| Velocidade do link | `Win32_NetworkAdapter.Speed` (convertido para Mbps) |

### 6.2 Probe ICMP

Utiliza `System.Net.NetworkInformation.Ping` com timeout de 1000 ms. O campo `RoundtripTime` da resposta é gravado em `ICMP_RTT_ms` no CSV.

### 6.3 Probe TCP (Conexão Assíncrona)

| Porta | Timeout | Comportamento em falha |
|---|---|---|
| Primária (Port1) | 1000 ms | `TIMEOUT` ou `NETWORK_FAILURE` (exceção .NET) |
| Secundária (Port2) | 500 ms | `TIMEOUT` ou `FAILURE` (exceção .NET) |

Ambas as portas medem a latência de conexão com um `Stopwatch` dedicado.

### 6.4 Intervalo de Ciclo Exato

Um `Stopwatch` de ciclo mede o tempo total gasto nas probes e dorme apenas o tempo restante até completar 1 segundo:

```powershell
$cycleWatch = [System.Diagnostics.Stopwatch]::StartNew()
# ... probes ...
$cycleWatch.Stop()
$remaining = 1000 - $cycleWatch.ElapsedMilliseconds
if ($remaining -gt 0) { Start-Sleep -Milliseconds $remaining }
```

---

## 7. Motor Heurístico de Causa Raiz

Os contadores heurísticos são atualizados **inline a cada ciclo**, sem necessidade de array de histórico:

| Contador | Incrementado quando |
|---|---|
| `$fwStress` | TCP1 falhou **e** ICMP respondeu (`OK`) |
| `$phyFail` | TCP1 falhou **e** ICMP também falhou |

### 7.1 Tabela de diagnósticos

| Condição | Diagnóstico gerado |
|---|---|
| `$lossPercent -eq 0` | **Normal Operation.** No anomalies detected. |
| `$phyFail > $fwStress` | **Physical Layer / EMI Disruption.** Simultaneous ICMP and TCP loss. |
| `$fwStress >= $phyFail` (com perda) | **Firmware Saturation (CPU Stress).** ICMP OK but TCP dropped. |
| Porta2 conectou durante falhas na Porta1 | Nota adicional de resiliência IEC 61850 MMS |

---

## 8. Relatório HTML e Gráfico de Latência

O relatório HTML é composto por quatro seções:

| Seção | Conteúdo |
|---|---|
| **1. Heuristic Root Cause Analysis** | Diagnóstico em destaque (caixa amarela) |
| **2. Hardware Audit Traceability** | IP alvo, IP origem, adaptador, MAC, velocidade do link, duração |
| **3. Statistical Metrics** | Enviados/recebidos, perda, latência média/mín/máx, StdDev, **PDV (RFC 3393)** |
| **4. Latency Profile Graph** | Gráfico Canvas responsivo com **duas linhas** + áreas de timeout |

### 8.1 Gráfico de Latência

O canvas é responsivo (`width: 100%`) e renderiza via JavaScript puro (sem bibliotecas):

| Elemento visual | Significado |
|---|---|
| Linha azul sólida (`#0072BD`) | TCP RTT — Porta 1 |
| Linha laranja tracejada (`#E87722`) | ICMP RTT |
| Áreas vermelhas semitransparentes | Ciclos com TIMEOUT ou FAILURE em qualquer porta |

O eixo Y escala automaticamente para o maior valor entre TCP RTT e ICMP RTT. O gráfico exibe **todas as amostras coletadas**, com exceção da primeira, que é descartada apenas do gráfico para evitar distorção no eixo Y causada pela latência anormalmente alta da conexão TCP inicial (resolução ARP / handshake a frio). A primeira amostra continua registrada normalmente no CSV.

**Detalhes de implementação do canvas:**

- Altura fixa de **220 px** — dimensionada para caber em uma página A4 portrait junto com as tabelas de métricas
- As áreas vermelhas de timeout são desenhadas com `ctx.save()` / `ctx.clip()` / `ctx.restore()` restritas ao retângulo do gráfico `(padL, padT, plotW, plotH)`, impedindo que ultrapassem as bordas do frame mesmo nos pontos extremos da série

---

## 9. Geração de PDF

O PDF é gerado via **Microsoft Edge em modo headless**. A detecção do executável usa três métodos em cascata:

```powershell
# 1. Caminhos de instalação padrão
$edge = @("C:\Program Files (x86)\...\msedge.exe", "C:\Program Files\...\msedge.exe") |
    Where-Object { Test-Path $_ } | Select-Object -First 1

# 2. Variável PATH do sistema
if (-not $edge) { $edge = (Get-Command msedge -ErrorAction SilentlyContinue)?.Source }

# 3. Registro do Windows
if (-not $edge) {
    $edge = (Get-ItemProperty "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\msedge.exe").'(default)'
}
```

Os arquivos de log temporários do Edge (`edge_out.log`, `edge_err.log`) são **removidos automaticamente** após a geração do PDF.

Se nenhum caminho for encontrado, a etapa é ignorada com aviso amarelo no terminal e o relatório HTML permanece disponível.

### 9.1 Layout de página

O HTML inclui as seguintes regras CSS para garantir que o relatório caiba em **uma única página A4 portrait**:

```css
@page { size: A4 portrait; margin: 10mm; }
@media print {
    body   { margin: 0; font-size: 11px; }
    h2     { margin-top: 10px; }
    .legend { font-size: 10px; padding: 6px; }
}
```

A regra `@page` suprime as margens padrão do Edge headless (que se somariam ao `margin: 40px` do `body` em tela), e os overrides de `@media print` colapsam o espaçamento de cabeçalhos e legenda apenas durante a impressão.

---

## 10. Métricas Estatísticas

Todas as métricas são calculadas com **consumo de memória constante** — sem acumulação de histórico em memória.

| Métrica | Algoritmo | Variáveis |
|---|---|---|
| Pacotes enviados/recebidos | Contadores incrementais | `$sent`, `$received` |
| Perda de pacotes (%) | `(sent - received) / sent × 100` | `$lossPercent` |
| Latência mínima / máxima | Comparação inline por ciclo | `$minLat`, `$maxLat` |
| Latência média | **Algoritmo de Welford** (online, O(1) de memória) | `$wfMean` |
| Jitter (StdDev) | Desvio padrão populacional via Welford M2 | `$wfM2` |
| **Jitter (PDV / RFC 3393)** | Média das diferenças absolutas entre latências consecutivas | `$pdvSum / $pdvCount` |

### Fórmulas finais

```powershell
$stdDev = [Math]::Sqrt($wfM2 / $n)                       # desvio padrão
$pdv    = $pdvSum / $pdvCount                             # PDV RFC 3393
```

A tabela de resumo no terminal exibe **PDV** como métrica de jitter principal (coluna `Jitter(PDV)`), alinhada com o padrão RFC 3393 utilizado em protocolos de tempo real como IEC 61850 GOOSE.

---

## 11. Como Usar

### Pré-requisito

Os dois arquivos devem estar na **mesma pasta**:

```
tcpTestV4.bat
TcpPingRelay.ps1
```

### Execução

1. Abrir `tcpTestV4.bat` com duplo clique ou via Prompt de Comando
2. Informar os parâmetros solicitados:
   ```
   1. Enter the Target IP (e.g., 192.168.1.23):              192.168.10.5
   2. Enter the Primary TCP Port (e.g., 1001):               1001
   3. Enter Secondary Port (e.g., 102 for MMS, 0 to skip):  102
   4. Execution time in seconds (e.g., 3600):                300
   ```
3. Aguardar o fim do teste ou pressionar `Q` para encerrar antecipadamente
4. Os relatórios serão salvos automaticamente em `<pasta do script>\Diagnostic_192.168.10.5_<timestamp>\`

### Exemplos de cenários de uso

| Cenário | Configuração sugerida |
|---|---|
| Teste rápido de conectividade | Duração: 60s, Porta2: 0 |
| Monitoramento de turno (8h) | Duração: 28800s |
| Diagnóstico IEC 61850 | Porta2: 102 (MMS); avaliar nota de resiliência no relatório |
| Verificação pós-manutenção | Duração: 300s; comparar CSV antes/depois |

### Leitura do terminal durante o teste

```
[28/05/2025 14:32:01] IP: 192.168.1.23 | P1(1001): CONNECTED | P2(102): CONNECTED | ICMP: OK | RTT: 4ms
```

- **Verde:** CONNECTED / OK
- **Vermelho:** TIMEOUT / FAIL / NETWORK_FAILURE / FAILURE

---

## 12. Histórico de Melhorias Implementadas

As 14 melhorias identificadas na análise inicial foram todas implementadas na refatoração da versão V5.

| # | Categoria | Melhoria | Status |
|---|---|---|---|
| 1 | Crítica | `TcpPingRelay.ps1` como arquivo permanente — elimina geração via `echo >>` | Implementado |
| 2 | Crítica | Validação de entradas no BAT (IPv4, portas, duração) | Implementado |
| 3 | Crítica | `Get-CimInstance` substitui `Get-WmiObject` depreciado | Implementado |
| 4 | Crítica | Auditoria de hardware executada no startup, independente de conexão TCP | Implementado |
| 5 | Funcional | Intervalo de ciclo exato via Stopwatch residual | Implementado |
| 6 | Funcional | ICMP RTT (`RoundtripTime`) coletado e gravado no CSV + exibido no gráfico | Implementado |
| 7 | Funcional | Algoritmo de Welford substitui array `$history` ilimitado | Implementado |
| 8 | Funcional | Latência da Porta2 medida com Stopwatch dedicado | Implementado |
| 9 | Funcional | Canvas HTML responsivo (`width: 100%`) | Implementado |
| 10 | Manutenção | Arquivos de log temporários do Edge removidos após PDF | Implementado |
| 11 | Manutenção | `-ExecutionPolicy RemoteSigned` substitui `Bypass` | Implementado |
| 12 | Manutenção | PDV (RFC 3393) calculado e exibido como métrica de jitter principal | Implementado |
| 13 | Manutenção | Detecção de IPv6 com mensagem de erro explícita | Implementado |
| 14 | Manutenção | Detecção robusta do Edge: caminhos padrão → `Get-Command` → registro | Implementado |
| 15 | Correção | `Find-NetRoute` substitui rota padrão na auditoria de hardware — seleciona o adaptador correto quando Wi-Fi e cabo estão ativos simultaneamente | Implementado |
| 16 | Correção | Zonas de timeout do gráfico limitadas ao frame via `ctx.clip()` — retângulos vermelhos não ultrapassam mais as bordas do canvas | Implementado |
| 17 | Correção | Layout de PDF ajustado com `@page` e `@media print` — relatório gerado em uma única página A4 portrait | Implementado |
| 18 | Funcional | Remoção do cap de 3.600 amostras no gráfico — todas as amostras são exibidas; primeira amostra descartada apenas do gráfico (mantida no CSV) para evitar distorção no eixo Y pelo RTT anômalo da sondagem inicial | Implementado |

### Alterações estruturais da refatoração

| Aspecto | Antes (V4) | Depois (V5) |
|---|---|---|
| Arquivos | 1 (`tcpTestV4.bat`) | 2 (`tcpTestV4.bat` + `TcpPingRelay.ps1`) |
| Geração do PS1 | ~200 linhas `echo >>` no BAT | PS1 permanente; BAT apenas invoca |
| Colunas no CSV | 6 | 8 (+ `ICMP_RTT_ms`, `TCP2_RTT_ms`) |
| Gráfico | 1 linha (TCP RTT) | 2 linhas (TCP RTT + ICMP RTT) |
| Métrica de jitter | StdDev populacional | StdDev + PDV (RFC 3393) |
| Auditoria de hardware | Só na 1ª conexão TCP bem-sucedida | No startup, via `Find-NetRoute` (interface que roteia o IP alvo) |
| Memória em teste longo | Cresce com `$history` | O(1) — Welford online |
| Detecção do Edge | 2 caminhos hardcoded | 3 métodos em cascata |
| Política de execução PS | `Bypass` | `RemoteSigned` |
| Pasta de saída | `%USERPROFILE%\Desktop\` | Mesma pasta do script (`%~dp0`) |
| Fonte do relatório HTML | `14px` (body), `13px` (legend) | `12px` (body), `11px` (legend) |

---

*Documentação atualizada em 18/06/2026 — reflete a versão V5 com todas as melhorias implementadas, incluindo melhoria #18 (remoção do cap de 3.600 amostras no gráfico).*

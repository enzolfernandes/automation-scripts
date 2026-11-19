# Fluke 8846A Serial Console

[English](#english) | [Português](#português)

## English

### Overview

This project provides a small interactive Windows PowerShell console for controlling a Fluke 8846A through its serial interface. It automatically detects the original Fluke 884X-USB FTDI adapter, verifies the meter with `*IDN?`, and then uses the detected COM port.

```text
fluke-8846a-console/
|- Console-Fluke8846A.ps1   # interactive serial console
|- README.md                # English and Portuguese documentation
`- references/              # instrument manuals and safety information
```

The console opens the serial port, places the instrument in remote mode before every command, provides shortcuts for common tasks, and returns the instrument to local mode when it exits. It does not calibrate the instrument, modify calibration data, or perform a factory reset.

### Requirements

- Windows with Windows PowerShell.
- A powered-on Fluke 8846A.
- An original Fluke 884X-USB cable connected to the computer and to the meter's rear RS-232 port.
- The cable assigned to any COM port in Windows. The console detects it automatically; see [Using another COM port](#using-another-com-port) only when automatic detection is unavailable or you need to choose a specific meter.

### Meter setup

On the meter front panel, open `INSTR SETUP` > `PORT IF`.

1. Under `SELECT PORT`, select `RS-232C`.
2. Open `RS-232C` and set the following values.

| Meter item | Value |
| --- | --- |
| Baud rate | `9600` |
| Data bits | `8` |
| Parity | `None` |
| Stop bits | `1` |
| Flow control | `None` |
| EOL | `CR LF` |
| `COMP/TERM ECHO` | `COMPUTER` |

Keep the standard **8845A/8846A (SCPI)** command language selected. The console sends commands such as `*IDN?` and `READ?`, terminated with `CR LF` to match this configuration.

> Only one remote interface can be active at a time. Select RS-232C for this project, not LAN or IEEE-488.

### Windows serial-port setup

In **Device Manager** > **Ports (COM & LPT)** > the detected `USB Serial Port (COMx)` > **Port Settings**, set:

| Setting | Value |
| --- | --- |
| Bits per second | `9600` |
| Data bits | `8` |
| Parity | `None` |
| Stop bits | `1` |
| Flow control | `None` |

These settings must match the meter.

### Start the console

1. Close any application using `COM3`, including a failed Tabby Serial tab.
2. Open **Windows PowerShell**.
3. Change to this project folder and run the console:

```powershell
Set-Location "C:\Users\NT-0967\Desktop\Enzo\repositórios\automation-scripts\fluke-8846a-console"
.\Console-Fluke8846A.ps1
```

If Windows blocks script execution, use the following command. It applies only to this run and does not change the permanent execution policy:

```powershell
powershell -ExecutionPolicy Bypass -File .\Console-Fluke8846A.ps1
```

You should see a message similar to:

```text
Detected and verified Fluke 8846A on COM3: FLUKE,8846A,...
Connected to Fluke 8846A through COM3.
Fluke>
```

You can also run the console in a regular PowerShell tab inside Tabby. Do not use Tabby's **Serial** profile on this computer: its serial component fails to open this FTDI adapter with error 121.

To check automatic detection and identity without opening the interactive prompt, run:

```powershell
.\Console-Fluke8846A.ps1 -VerifyOnly
```

### Daily use

At the `Fluke>` prompt, enter a shortcut or any SCPI command.

| Input | Action |
| --- | --- |
| `port` | Shows the selected COM port and detected device. |
| `id` | Displays manufacturer, model, serial number, and software version. |
| `read` | Takes and returns a reading using the meter's current configuration. |
| `config` | Shows the current measurement configuration. |
| `error` / `errors` | Reads the next error or drains the complete error queue. |
| `status` / `event` | Reads the status-byte or standard-event register. `event` clears the event register after reading it. |
| `clear` | Clears the error queue and event/status registers. |
| `sample` / `trigger` | Shows the configured sample count, trigger count, and trigger source. |
| `display on`, `display off`, `display clear` | Controls the front-panel display. |
| `display text <message>` | Shows a message on the front-panel display. |
| `local` | Immediately returns control to the front panel. |
| `exit` | Returns the meter to local mode, closes the COM port, and exits. |

Examples of direct SCPI commands:

```text
*IDN?
SYST:ERR?
READ?
CONF?
```

Measurement shortcuts are also available. Each shortcut changes the active measurement function to its preset configuration before taking the reading:

```text
dcv      acv      dci      aci
resistance   fres   frequency   period
capacitance  continuity  diode
```

Commands ending in `?` are queries and normally return data. Commands without `?` configure or trigger a function and might not return text.

### Common responses

| Response | Meaning |
| --- | --- |
| `FLUKE,8846A,...` | Serial communication is confirmed. |
| `+0,"No error"` | No queued errors. |
| `+550,"Command not allowed in local"` | The instrument was in local mode. The console sends `SYST:REM` before regular commands to prevent this. |
| `+9.90000000E+37` | This is not a physical reading. It is a special open/out-of-range value, commonly seen when resistance or diode mode has no device connected. Check the selected function and test leads. |
| `No Fluke 884X-USB adapter was detected` | The adapter is disconnected, not recognized by Windows, or is not using the expected FTDI driver. Reconnect it or supply the port explicitly. |

### Using another COM port

Automatic detection is the normal path. If the adapter is not detected automatically, or if more than one compatible meter is connected, select the port explicitly. For `COM4`, run:

```powershell
.\Console-Fluke8846A.ps1 -Port COM4
```

### Safety and troubleshooting

- `READ?` uses the meter's current measurement function. Confirm the function, range, and lead connections before taking a measurement.
- Do not send `CAL...` commands; they are calibration-related.
- Avoid `*RST` during an active measurement. It restores the meter's power-on configuration, although it is not a factory calibration reset.
- Use `exit` when finished so the serial port is released correctly.
- If access to `COM3` is denied, close the application holding that port and try again.
- If there is no response to `id`, confirm that RS-232C is selected and that both sides use 9600, 8N1, no flow control, and `CR LF`.

### References

The original reference files were copied to [`references/`](references/) without removing their source folder. Their project-folder and PDF filenames were translated to English; the PDF contents are unchanged.

| Folder | Contents |
| --- | --- |
| `01_Operation` | User manual and supplement. |
| `02_Calibration` | Calibration manual and supplement. |
| `03_Programming` | SCPI/RS-232 programming manual and supplement. |
| `04_Electrical_Safety` | Electrical safety information and supplement. |
| `05_Data_Security` | Instrument data-security procedures. |

Read each supplement together with its corresponding primary manual because supplements correct or update the main edition.

| Folder | Documents |
| --- | --- |
| `01_Operation` | User Manual - Rev. 3 (2011), 104 pages; User Manual Supplement - Ed. 7 (2021), 4 pages. Covers installation, front-panel use, specifications, measurements, interfaces, and the later fuse/safety/EMC updates. |
| `02_Calibration` | Calibration Manual - Rev. 1 (2007), 118 pages; Calibration Manual Supplement - Ed. 13 (2015), 10 pages. Covers performance tests, adjustment/calibration, replacement parts, and later corrections. |
| `03_Programming` | Programmers Manual - Rev. 3 (2011), 126 pages; Programmers Manual Supplement - Ed. 1 (2013), 2 pages. Covers IEEE-488/GPIB, RS-232, LAN, SCPI, triggers, status registers, and 8842A emulation. |
| `04_Electrical_Safety` | Safety Information (2006), 2 pages; Safety Information Supplement - Ed. 2 (2019), 2 pages. Covers shock warnings, voltage limits, fuses, safe operation, and EMC updates. |
| `05_Data_Security` | Instrument Security Procedures (2006), 2 pages. Covers volatile/non-volatile memory and erasure of internal and USB memory. |

Recommended reading order: safety first; the User Manual for normal operation; the Programmers Manual for automation; the Calibration Manual for metrological maintenance; and the data-security procedures before transfer, disposal, or service.

The console configuration and commands are based mainly on the *Fluke 8845A/8846A Programmers Manual*, Rev. 3 (2011): RS-232 setup, the `*IDN?` installation test, `SYST:REM`/`SYST:LOC`, and error codes.

---

## Português

### Visão geral

Este projeto fornece um pequeno console interativo em Windows PowerShell para controlar um Fluke 8846A pela interface serial. Ele detecta automaticamente o adaptador FTDI do cabo original Fluke 884X-USB, confirma o multímetro com `*IDN?` e então usa a porta COM encontrada.

```text
fluke-8846a-console/
|- Console-Fluke8846A.ps1   # console serial interativo
|- README.md                # documentação em inglês e português
`- references/              # manuais e informações de segurança
```

O console abre a porta serial, coloca o instrumento em modo remoto antes de cada comando, oferece atalhos para tarefas comuns e devolve o instrumento ao modo local ao encerrar. Ele não calibra o instrumento, não altera os dados de calibração e não executa um reset de fábrica.

### Requisitos

- Windows com Windows PowerShell.
- Fluke 8846A ligado.
- Cabo Fluke 884X-USB original conectado ao computador e à porta RS-232 traseira do multímetro.
- Cabo atribuído a qualquer porta COM no Windows. O console o detecta automaticamente; consulte [Usar outra porta COM](#usar-outra-porta-com) apenas quando a detecção automática não estiver disponível ou for necessário escolher um multímetro específico.

### Configuração do multímetro

No painel frontal, abra `INSTR SETUP` > `PORT IF`.

1. Em `SELECT PORT`, escolha `RS-232C`.
2. Abra `RS-232C` e defina os valores abaixo.

| Item do multímetro | Valor |
| --- | --- |
| Baud rate | `9600` |
| Data bits | `8` |
| Parity | `None` |
| Stop bits | `1` |
| Flow control | `None` |
| EOL | `CR LF` |
| `COMP/TERM ECHO` | `COMPUTER` |

Mantenha selecionada a linguagem padrão **8845A/8846A (SCPI)**. O console envia comandos como `*IDN?` e `READ?`, terminados em `CR LF` para corresponder a esta configuração.

> Apenas uma interface remota pode estar ativa por vez. Para este projeto, selecione RS-232C, não LAN nem IEEE-488.

### Configuração da porta no Windows

Em **Gerenciador de Dispositivos** > **Portas (COM e LPT)** > a `USB Serial Port (COMx)` detectada > **Configurações da Porta**, defina:

| Item | Valor |
| --- | --- |
| Bits por segundo | `9600` |
| Bits de dados | `8` |
| Paridade | `Nenhuma` |
| Bits de parada | `1` |
| Controle de fluxo | `Nenhum` |

Esses valores devem corresponder aos do multímetro.

### Iniciar o console

1. Feche programas que estejam usando a `COM3`, incluindo uma aba Serial do Tabby que tenha falhado.
2. Abra o **Windows PowerShell**.
3. Entre na pasta do projeto e inicie o console:

```powershell
Set-Location "C:\Users\NT-0967\Desktop\Enzo\repositórios\automation-scripts\fluke-8846a-console"
.\Console-Fluke8846A.ps1
```

Se o Windows bloquear a execução de scripts, use este comando. Ele vale apenas para esta execução e não altera a política permanente:

```powershell
powershell -ExecutionPolicy Bypass -File .\Console-Fluke8846A.ps1
```

Você verá uma mensagem semelhante a:

```text
Detected and verified Fluke 8846A on COM3: FLUKE,8846A,...
Connected to Fluke 8846A through COM3.
Fluke>
```

Também é possível executar o console em uma aba normal de PowerShell dentro do Tabby. Não use o perfil **Serial** do Tabby neste computador: o componente serial dele falha ao abrir este adaptador FTDI com o erro 121.

Para conferir a detecção automática e a identidade sem abrir o prompt interativo, execute:

```powershell
.\Console-Fluke8846A.ps1 -VerifyOnly
```

### Uso diário

No prompt `Fluke>`, digite um atalho ou qualquer comando SCPI.

| Digite | Ação |
| --- | --- |
| `port` | Exibe a porta COM selecionada e o dispositivo detectado. |
| `id` | Exibe fabricante, modelo, número de série e versão de software. |
| `read` | Faz e retorna uma leitura com a configuração atual do multímetro. |
| `config` | Exibe a configuração de medição atual. |
| `error` / `errors` | Lê o próximo erro ou esvazia a fila completa de erros. |
| `status` / `event` | Lê o registro de status ou de evento padrão. `event` limpa o registro de eventos após a leitura. |
| `clear` | Limpa a fila de erros e os registros de evento/status. |
| `sample` / `trigger` | Exibe quantidade de amostras, quantidade de gatilhos e fonte de gatilho configuradas. |
| `display on`, `display off`, `display clear` | Controla o display do painel frontal. |
| `display text <mensagem>` | Mostra uma mensagem no display do painel frontal. |
| `local` | Devolve imediatamente o controle ao painel frontal. |
| `exit` | Devolve o instrumento ao modo local, fecha a COM e encerra. |

Exemplos de comandos SCPI diretos:

```text
*IDN?
SYST:ERR?
READ?
CONF?
```

Também existem atalhos de medição. Cada atalho altera a função de medição ativa para a configuração predefinida antes de realizar a leitura:

```text
dcv      acv      dci      aci
resistance   fres   frequency   period
capacitance  continuity  diode
```

Comandos terminados em `?` são consultas e normalmente retornam dados. Comandos sem `?` configuram ou acionam uma função e podem não exibir texto.

### Respostas comuns

| Resposta | Significado |
| --- | --- |
| `FLUKE,8846A,...` | A comunicação serial está confirmada. |
| `+0,"No error"` | Não há erros pendentes. |
| `+550,"Command not allowed in local"` | O instrumento estava em modo local. O console envia `SYST:REM` antes dos comandos normais para evitar isso. |
| `+9.90000000E+37` | Não é uma leitura física. É um valor especial para condição aberta/fora de faixa, comum em resistência ou diodo sem dispositivo conectado. Confira a função selecionada e as pontas de prova. |
| `No Fluke 884X-USB adapter was detected` | O adaptador está desconectado, não foi reconhecido pelo Windows ou não usa o driver FTDI esperado. Reconecte-o ou informe a porta explicitamente. |

### Usar outra porta COM

A detecção automática é o caminho normal. Se o adaptador não for detectado automaticamente, ou se houver mais de um multímetro compatível conectado, escolha a porta explicitamente. Para a `COM4`, execute:

```powershell
.\Console-Fluke8846A.ps1 -Port COM4
```

### Segurança e diagnóstico

- `READ?` usa a função de medição atual. Confirme função, faixa e conexões antes de medir.
- Não envie comandos `CAL...`: eles são relacionados à calibração.
- Evite `*RST` durante uma medição ativa. Ele restaura a configuração de inicialização, embora não seja um reset de calibração de fábrica.
- Use `exit` ao terminar para liberar corretamente a porta serial.
- Se o acesso à `COM3` for negado, feche o aplicativo que mantém a porta aberta e tente novamente.
- Se não houver resposta a `id`, confirme RS-232C selecionada e ambos os lados em 9600, 8N1, sem controle de fluxo e `CR LF`.

### Referências

Os arquivos originais de consulta foram copiados para [`references/`](references/), sem remover a pasta de origem. Os nomes das pastas e dos PDFs no projeto foram traduzidos para inglês; o conteúdo dos PDFs não foi alterado.

| Pasta | Conteúdo |
| --- | --- |
| `01_Operation` | Manual do usuário e suplemento. |
| `02_Calibration` | Manual de calibração e suplemento. |
| `03_Programming` | Manual de programação SCPI/RS-232 e suplemento. |
| `04_Electrical_Safety` | Informações e suplemento de segurança elétrica. |
| `05_Data_Security` | Procedimentos de segurança de dados do instrumento. |

Consulte cada suplemento junto com o respectivo manual principal, pois os suplementos corrigem ou atualizam a edição base.

| Pasta | Documentos |
| --- | --- |
| `01_Operation` | User Manual - Rev. 3 (2011), 104 páginas; User Manual Supplement - Ed. 7 (2021), 4 páginas. Abrange instalação, painel frontal, especificações, medições, interfaces e atualizações posteriores de fusíveis/segurança/EMC. |
| `02_Calibration` | Calibration Manual - Rev. 1 (2007), 118 páginas; Calibration Manual Supplement - Ed. 13 (2015), 10 páginas. Abrange testes de desempenho, ajustes/calibração, peças e correções posteriores. |
| `03_Programming` | Programmers Manual - Rev. 3 (2011), 126 páginas; Programmers Manual Supplement - Ed. 1 (2013), 2 páginas. Abrange IEEE-488/GPIB, RS-232, LAN, SCPI, gatilhos, registros de status e emulação 8842A. |
| `04_Electrical_Safety` | Safety Information (2006), 2 páginas; Safety Information Supplement - Ed. 2 (2019), 2 páginas. Abrange alertas de choque, limites de tensão, fusíveis, operação segura e atualizações EMC. |
| `05_Data_Security` | Instrument Security Procedures (2006), 2 páginas. Abrange memória volátil/não volátil e apagamento de memória interna e USB. |

Ordem recomendada de leitura: segurança primeiro; User Manual para uso normal; Programmers Manual para automação; Calibration Manual para manutenção metrológica; e procedimentos de segurança de dados antes de transferência, descarte ou serviço.

A configuração e os comandos deste console baseiam-se principalmente no *Fluke 8845A/8846A Programmers Manual*, Rev. 3 (2011): configuração RS-232, teste `*IDN?`, comandos `SYST:REM`/`SYST:LOC` e códigos de erro.

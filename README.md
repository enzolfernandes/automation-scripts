# Laboratory automation tools / Ferramentas de automação de laboratório

Português: Defini os requisitos, orientei o desenvolvimento com apoio de IA e testei e ajustei estas ferramentas para rotinas de laboratório. Os resultados funcionais e limites estão documentados por projeto. Não foram medidos ganhos de produtividade.

English: I defined requirements, guided AI-assisted development, and tested and refined these tools for laboratory workflows. Functional outcomes and limitations are documented per project. Productivity gains have not been measured.

| Project | Purpose |
|---|---|
| [AMT205 Thermal Validator](AMT205%20Thermal%20Validator/) | Python thermal-curve analysis, configurable criteria and Excel reports |
| [RT430 PTP Active Probe](RT430%20PTP%20Active%20Probe/) | Python IEEE 1588 peer-delay diagnostics; documented functional synchronization result |
| [bulkWordCreator](bulkWordCreator/) | Excel-to-Word test-report generation using fictional example data |
| [Fluke 8846A console](fluke-8846a-console/) | PowerShell RS-232/SCPI communication with a bench multimeter |
| [Local Channel Diagnostics](local-channel-diagnostics/) | PowerShell local-port/process and functional-check diagnostics |
| [NetDiag](NetDiag/) | TCP/ICMP diagnostics and CSV/HTML/PDF reports |

## Public package

This snapshot contains source, documentation and synthetic tests. Raw bench captures, generated reports, customer records, personal session values and the original private history are not included. The original private development history is preserved separately. The publication preparation does not count as an additional instrument test.

Additional utilities: `combinarPlanilhas.py` combines Excel files into separate workbook sheets; `backup_moodle.py` uses a user-provided local `MOODLE_SESSION` environment value. Only access systems and equipment you are authorized to use.

Manufacturer reference manuals retain their original attribution. No repository-wide software license is selected by this publication preparation.

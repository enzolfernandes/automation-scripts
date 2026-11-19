# NetDiag — meu diagnóstico de comunicação no Windows

Desenvolvo esta ferramenta em PowerShell e Batch para reunir sondagens TCP/ICMP e relatórios de comunicação. O código coleta disponibilidade e latência, registra dados em CSV e gera um relatório HTML; a exportação para PDF utiliza o Microsoft Edge quando disponível.

## Arquivos atuais

| Arquivo | Papel |
| --- | --- |
| [NetDiag.bat](NetDiag.bat) | Entrada interativa e parâmetros de execução |
| [NetDiag.Engine.ps1](NetDiag.Engine.ps1) | Sondagens, coleta e relatórios |

Mantenha os dois arquivos na mesma pasta. O código requer Windows e PowerShell 5.1 ou superior. A geração de PDF depende do Edge. Restrições locais de execução de scripts podem exigir adequação à política do ambiente.

## Executar

1. Baixe o repositório ou os dois arquivos acima.
2. Abra `NetDiag.bat` no Windows.
3. Informe o IPv4 autorizado para diagnóstico, a porta TCP principal, a porta secundária e a duração em segundos solicitados pelo menu.
4. Ao término, confira os arquivos CSV e HTML e o PDF, quando disponível, na pasta `Diagnostic_IP_timestamp` criada ao lado do script.

A segunda porta TCP usa 102 como padrão para MMS/IEC 61850; o menu aceita 0 para ignorá-la. Verifique o alvo e os parâmetros no ambiente de uso.

## Como interpreto os resultados

O motor combina sondagens TCP e ICMP, dados do adaptador e estatísticas para ajudar na investigação de falhas. As classificações de causa são heurísticas: considero o contexto da rede e as demais evidências ao interpretá-las. O intervalo programado de coleta não é uma garantia de tempo real.

## Histórico e documentação técnica

O [documento técnico V5](DOCUMENTATION.md) registra arquitetura, métricas e histórico de desenvolvimento. Ele ainda contém nomes de versões anteriores. Para executar a versão atual, siga este README e use esta correspondência:

| Nome anterior no documento técnico | Arquivo atual |
| --- | --- |
| `tcpTestV4.bat` | `NetDiag.bat` |
| `TcpPingRelay.ps1` | `NetDiag.Engine.ps1` |

Meu [histórico do repositório](https://github.com/enzolfernandes/automation-scripts/commits/main/NetDiag/) permite acompanhar a implementação. Acrescentei este guia em 17/10/2026 para alinhar as instruções aos arquivos versionados; não executei sondagens, medi desempenho ou validei a ferramenta em uma rede nesta revisão documental.

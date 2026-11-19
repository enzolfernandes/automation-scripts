# RT430 PTP Active Probe

Sonda ativa para diagnosticar a troca de mensagens `Pdelay` entre uma interface Ethernet Windows e um grandmaster GE Reason RT430. A ferramenta gera `Pdelay_Req`, captura as respostas e conserva PCAP e JSON para auditoria.

Versão arquivada: `0.1.0`  
Identificação: `TDM-SW-PTP-001`  
Status: `EM REVISÃO`

## Finalidade e limite

O programa responde à pergunta: **o RT430 recebeu e respondeu ao pedido Pdelay observável neste ponto da rede?**

Ele não mede precisão temporal nem substitui um analisador PTP com timestamp de hardware. Um resultado `NO_RESPONSE` também não prova sozinho defeito no grandmaster: switches, conversores e filtros de camada 2 podem impedir a entrega ou o retorno do quadro.

## Configuração validada em 26/08/2026

- GE Reason RT430, Power Profile IEEE C37.238-2017;
- IEEE 1588v2 diretamente sobre Ethernet (`0x88F7`);
- domínio 10, VLAN 400 e prioridade 6;
- endereço peer-delay `01:80:C2:00:00:0E`;
- interface testada `Realtek PCIe GbE Family Controller`;
- RT430 observado no MAC `F8:02:78:10:B7:AE`.

Na validação de campo, o primeiro ensaio, com um Multilin ML600 no caminho, não obteve respostas. Após retirar esse equipamento intermediário, a sonda recebeu respostas nos modos peer e multicast geral. A captura final da mala Vebko registrou 92 sequências completas de `Pdelay_Req`/`Pdelay_Resp`/`Pdelay_Resp_Follow_Up`, e o software indicou `Detected/Time is Valid`.

Consulte [relatório de validação](docs/VALIDATION_REPORT_2026-08-26.md) e [referência do protocolo](docs/PROTOCOL_REFERENCE.md).

## Pré-requisitos

1. Windows com Python 3.10 ou superior.
2. Npcap instalado com suporte à interface Ethernet usada.
3. PowerShell ou Prompt de Comando executado como Administrador para transmissão.
4. Dependências:

```powershell
python -m pip install -r .\requirements.txt
```

## Uso

No PowerShell, entre nesta pasta:

```powershell
cd "C:\Users\NT-0967\Desktop\Vebko\Software\RT430 PTP Active Probe"
```

Liste as interfaces disponíveis:

```powershell
python .\rt430_ptp_probe.py --list-interfaces
```

Valide os quadros sem transmitir:

```powershell
python .\rt430_ptp_probe.py `
  --iface "Realtek PCIe GbE Family Controller" `
  --dry-run
```

Execute a matriz validada:

```powershell
python .\rt430_ptp_probe.py `
  --iface "Realtek PCIe GbE Family Controller" `
  --rt-mac "F8:02:78:10:B7:AE" `
  --dest all `
  --domain 10 `
  --vlan 400 `
  --pcp 6 `
  --count 10
```

O lançador `Run RT430 Probe as Administrator.bat` executa essa configuração específica. Revise seus parâmetros antes de reutilizá-lo com outro RT430 ou computador.

## Modos de destino

| Modo | Destino | Uso |
|---|---|---|
| `peer` | `01:80:C2:00:00:0E` | Endereço padrão de peer delay, com escopo de link |
| `general` | `01:1B:19:00:00:00` | Multicast PTP geral observado nos testes |
| `unicast` | MAC informado em `--rt-mac` | Diagnóstico complementar com `unicastFlag` |
| `all` | Os três anteriores | Matriz completa |

Para reproduzir a variação legada observada na mala de MAC final `11:99`:

```powershell
python .\rt430_ptp_probe.py `
  --iface "Realtek PCIe GbE Family Controller" `
  --dest general `
  --legacy-two-step-flag `
  --legacy-log-interval `
  --count 10
```

## Saídas

Cada execução cria em `results`:

- `*_sent.pcap`: solicitações geradas/transmitidas;
- `*_capture.pcap`: tráfego capturado durante a janela do ensaio;
- `*.json`: configuração, respostas correlacionadas e sequências sem resposta.

Interpretação do campo `result`:

- `DRY_RUN`: quadros gerados, sem transmissão;
- `RESPONSE_RECEIVED`: pelo menos um `Pdelay_Resp` foi correlacionado;
- `NO_RESPONSE`: nenhuma resposta correlacionada foi observada.

Arquive cada ensaio relevante fora de `results`, com data, topologia física, versão dos equipamentos e capturas originais.

## Verificação e versão

```powershell
python .\rt430_ptp_probe.py --version
python -m unittest -v .\test_rt430_ptp_probe.py
```

## Estrutura arquivada

```text
docs/          documentação técnica e relatório de validação
releases/      pacotes gerados localmente (não distribuídos aqui)
results/       saída temporária de novas execuções
docs/VALIDATION_REPORT_2026-08-26.md — resumo público; capturas brutas privadas
```

O histórico de alterações está em `CHANGELOG.md`; a identificação e o fluxo de aprovação estão em `DOCUMENT_CONTROL.md`.

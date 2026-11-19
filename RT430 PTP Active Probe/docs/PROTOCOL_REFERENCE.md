# Referência técnica dos quadros

## Escopo

Esta nota descreve os campos usados pelo RT430 PTP Active Probe v0.1.0. Ela serve para revisar PCAPs e reproduzir o ensaio, não como declaração de conformidade integral com IEEE 1588 ou IEEE C37.238.

## Encapsulamento padrão

| Camada/campo | Valor padrão | Observação |
|---|---:|---|
| Ethernet EtherType externo | `0x8100` | Tag IEEE 802.1Q |
| VLAN ID | `400` | Configurável por `--vlan` |
| PCP | `6` | Configurável por `--pcp` |
| EtherType interno | `0x88F7` | PTPv2 sobre Ethernet Layer 2 |
| PTP transportSpecific | `1` | Perfil de potência |
| PTP messageType | `2` | `Pdelay_Req` |
| PTP version | `2` | IEEE 1588v2 |
| Domain | `10` | Configurável por `--domain` |
| Source port | `1` | Configurável por `--source-port` |
| Control field | `5` | Outros tipos de mensagem |
| logMessageInterval | `127` | Valor padrão para o pedido; modo legado usa `0` |

A identidade de relógio de origem é derivada do MAC no formato EUI-64, inserindo `FF:FE` entre os três primeiros e os três últimos octetos. Exemplo: `B4:45:06:90:5D:5F` gera `B4:45:06:FF:FE:90:5D:5F`.

## Destinos avaliados

### Peer delay

`01:80:C2:00:00:0E` é o endereço multicast de escopo local usado pelo mecanismo P2P. Por ser link-local, sua passagem através de bridges/conversores depende do comportamento do equipamento intermediário. Essa característica explica por que a topologia deve acompanhar todo resultado negativo.

### Multicast PTP geral

`01:1B:19:00:00:00` é um destino multicast geral de PTP. Foi incluído como variação diagnóstica porque apareceu nos ensaios com a mala Vebko e normalmente pode atravessar uma bridge comum.

### Unicast

O modo `unicast` endereça o MAC informado para o RT430 e habilita o `unicastFlag`. É uma tentativa diagnóstica; a ausência de resposta não significa que o equipamento esteja obrigado a aceitar esse modo na configuração ativa.

## Correlação de respostas

Uma resposta é considerada relacionada ao pedido quando os campos observáveis são compatíveis:

- domínio PTP;
- `requestingPortIdentity` correspondente à identidade e à porta de origem;
- `sequenceId` transmitido;
- tipo `Pdelay_Resp` ou `Pdelay_Resp_Follow_Up`.

O relatório JSON preserva as sequências respondidas e não respondidas. A captura PCAP continua sendo a fonte primária para revisão independente.

## Limitações

- Os timestamps são de software, não de hardware.
- A sonda não calcula erro do relógio nem certifica sincronismo.
- A captura ocorre na interface local e não comprova que um quadro chegou ao outro lado de um equipamento intermediário.
- Offload da placa, Npcap, filtros do driver e concorrência com outros capturadores podem influenciar a observação.
- Para investigação conclusiva de uma perda em trânsito, use TAP Ethernet ou captura simultânea nos dois lados.

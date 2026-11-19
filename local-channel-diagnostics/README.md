# Diagnóstico de Canais Locais

Ferramenta com interface gráfica para diagnosticar canais de comunicação que utilizam portas TCP e UDP no Windows. Ela ajuda a diferenciar uma porta simplesmente aberta de um serviço que realmente responde e de uma operação que funciona de ponta a ponta.

## Objetivo

O programa foi criado para reduzir a troca de portas por tentativa e erro. Ele reúne em uma única interface:

- análise das portas configuradas;
- identificação de conexões, processos e possíveis conflitos;
- observação não invasiva dos canais reais;
- teste ativo opcional para investigação avançada;
- monitoramento de mudanças durante a execução do aplicativo;
- busca de novos pares de portas disponíveis;
- confirmação manual do funcionamento real;
- histórico da sessão e exportação de relatório.

O programa não altera automaticamente a configuração de outros aplicativos. Quando um novo par é escolhido, os mesmos números ainda precisam ser configurados no aplicativo analisado.

## Como abrir

Use o atalho **Diagnóstico de Canais Locais** disponível na área de trabalho.

Arquivos principais:

- `Local-Channel-Diagnostics.ps1`: programa e lógica de diagnóstico;
- `Launch-Local-Channel-Diagnostics.vbs`: inicializador que abre a interface sem exibir uma janela de terminal;
- `Diagnostico de Canais Locais.lnk`: atalho da área de trabalho.

## As três camadas de verificação

Os três indicadores superiores representam verificações diferentes. Uma camada aprovada não garante automaticamente que a próxima também esteja funcionando.

### 1. Camada de transporte

Verifica o estado local das portas no Windows:

- endpoints TCP associados à porta;
- endpoints UDP associados à porta;
- conexões TCP em estado de escuta ou estabelecidas;
- processo proprietário da porta;
- processos clientes conectados;
- resposta dos processos ao Windows;
- possibilidade de reservar a porta para TCP e UDP;
- utilização da porta por um processo inesperado.

Principais resultados:

- **Conectado:** os dois canais apresentam conexão TCP estabelecida.
- **Atenção:** o serviço está presente, mas nem todos os canais estão estabelecidos.
- **Falha:** foi encontrado conflito ou processo sem resposta.
- **Não verificado:** nenhum processo alvo está em execução.
- **Livre:** a porta pôde ser reservada temporariamente para TCP e UDP.

Uma conexão TCP estabelecida comprova o canal de transporte, mas não comprova que o serviço processou corretamente uma solicitação.

### 2. Teste de comunicação

No modo padrão, esta camada observa as conexões que já existem entre o aplicativo real e o serviço local. Quando os dois lados esperados estão identificados e a conexão TCP está estabelecida, o resultado é **Operante por observação**. Nenhum cliente adicional é aberto nesse caso.

Essa abordagem evita que um cliente de diagnóstico concorra com o cliente real ou produza uma falha auxiliar enquanto o canal usado pelo aplicativo continua funcionando.

O envio de uma solicitação pelo protocolo do serviço ficou reservado ao modo **Teste ativo (avançado)**. Esse teste pode confirmar que houve uma troca de dados, mas só é conclusivo se a resposta recebida for reconhecida. Uma resposta de comando não suportado ou de conteúdo desconhecido é apresentada como **Inconclusiva**, e não como sucesso.

Se TCP e UDP estiverem livres, o programa não tenta obter uma resposta inexistente. Nesse caso, apresenta **Disponível — aguardando configuração** em vez de falha.

Cada porta é testada em um processo auxiliar isolado. O teste possui limite de **6 segundos por porta**. Se uma porta aceitar a conexão e não devolver dados:

- o processo auxiliar é encerrado;
- o resultado auxiliar recebe um diagnóstico de tempo limite ou inconclusivo;
- a interface principal permanece responsiva;
- o teste continua ou termina sem ficar aguardando indefinidamente.

Com duas portas sem resposta, o teste avançado pode levar aproximadamente 12 segundos. Durante esse período, a mensagem inferior mostra qual porta está sendo testada.

Se o canal real continuar conectado, uma falha do cliente auxiliar não rebaixa esse canal para falha. Sem um canal real conectado, uma falha ativa só é confirmada após três ocorrências consecutivas; as anteriores são marcadas como inconclusivas.

### 3. Teste funcional

É a confirmação do resultado observado em uma operação real no aplicativo analisado.

- Clique em **Funcionou** quando a operação real for concluída corretamente.
- Clique em **Falhou** quando a infraestrutura parecer normal, mas a operação real não funcionar.

Essa confirmação é necessária porque portas conectadas e testes auxiliares respondendo ainda podem coexistir com falhas em outra camada da aplicação, na configuração ou em elementos externos ao computador.

## Botão Verificar agora

No modo recomendado, executa uma verificação segura em duas etapas:

1. analisa portas, conexões e processos;
2. identifica se os canais reais esperados já estão estabelecidos, sem abrir clientes adicionais.

Durante a execução:

- o botão fica temporariamente desabilitado para evitar testes duplicados;
- o monitoramento periódico é pausado, caso esteja ativo;
- a interface continua processando eventos do Windows;
- o monitoramento é retomado automaticamente ao final.

Depois da verificação, consulte os três indicadores e a recomendação exibida na parte inferior da visão geral.

Uma porta livre é tratada como candidata para configuração. Ela não recebe teste de serviço até que o aplicativo seja configurado, reiniciado e passe a utilizar a porta.

## Modos de diagnóstico

O campo **Modo** permite escolher a profundidade da verificação:

- **Seguro (recomendado):** analisa disponibilidade, conexões, processos e recursos do Windows; quando encontra os dois lados esperados conectados, classifica o canal como operante por observação. É o modo padrão e não envia comando de teste.
- **Somente portas:** verifica disponibilidade, proprietários, reservas e recursos do Windows sem enviar um teste ativo.
- **Conexão TCP:** tenta estabelecer uma conexão TCP local, mas não valida o protocolo do serviço.
- **Teste ativo (avançado):** abre um cliente auxiliar e tenta uma troca de dados pelo protocolo compatível. Use apenas quando precisar investigar além da observação passiva.

Ao trocar o modo, resultados ativos anteriores são descartados para não misturar conclusões de testes diferentes. O modo escolhido é mantido para a próxima abertura.

### Cancelar uma verificação

O botão **Cancelar** fica disponível enquanto uma verificação está em andamento. Ao acioná-lo:

- o pedido de cancelamento é sinalizado imediatamente;
- o processo auxiliar do teste de serviço é encerrado;
- conexões TCP em andamento são interrompidas;
- a interface mostra **Cancelado** em vez de falha;
- o monitoramento anterior é retomado normalmente.

O cancelamento do teste isolado foi projetado para responder em poucos milissegundos, sem aguardar o limite normal da porta.

## Monitoramento

O botão **Iniciar monitoramento** atualiza periodicamente os estados das portas, conexões e processos. Ele é útil para observar:

- abertura e fechamento do aplicativo;
- criação ou encerramento de uma conexão TCP;
- falhas intermitentes;
- troca do processo proprietário da porta;
- mudança entre porta livre, escutando e conectada.

O botão muda para **Parar monitoramento** enquanto estiver ativo. O monitoramento acompanha a infraestrutura local; ele não executa continuamente uma operação funcional no aplicativo.

Resultados recentes da observação ou do teste ativo permanecem associados às portas por até cinco minutos. Depois disso, uma nova verificação deve ser executada.

## Localizar um novo par de portas

A área **Localizar novo par de portas** contém:

- **Início:** primeira região de portas a ser pesquisada;
- **Diferença:** distância numérica entre a primeira e a segunda porta;
- **Sugestões:** quantidade máxima de pares a apresentar.

O programa procura portas que possam ser reservadas localmente tanto em TCP quanto em UDP. A primeira combinação encontrada é destacada como recomendação inicial.

Na aba **Detalhes técnicos**, é possível:

- selecionar um par sugerido;
- carregar o par para a área de diagnóstico;
- copiar os números para a área de transferência.

Antes de carregar um par selecionado, a ferramenta testa novamente as duas portas em TCP e UDP. Se alguma delas tiver sido ocupada desde a pesquisa, o par é marcado como indisponível e não é carregado.

### Importante sobre portas livres

O resultado é válido apenas no momento da pesquisa. Uma porta livre pode ser utilizada posteriormente por outro processo. Depois de aplicar o novo par no aplicativo:

1. reinicie o aplicativo;
2. execute **Verificar agora**;
3. confirme a operação real;
4. monitore por alguns instantes se a falha for intermitente.

## Visão geral

A aba **Visão geral** concentra:

- portas atualmente analisadas;
- botão de verificação completa;
- controle do monitoramento;
- busca de pares livres;
- próxima ação recomendada;
- histórico de alterações da sessão.

## Detalhes técnicos

A aba **Detalhes técnicos** apresenta uma linha para cada porta, incluindo:

- estado TCP;
- estado UDP;
- processos servidores e clientes;
- identificadores dos processos;
- resposta do processo ao Windows;
- diagnóstico consolidado.

As cores auxiliam na leitura:

- verde: canal real observado ou conexão aceita;
- azul: conexão ou porta candidata;
- amarelo: condição de atenção;
- vermelho: falha ou conflito.

Uma legenda bilíngue permanece visível no cabeçalho da ferramenta para identificar rapidamente os quatro níveis de cor.

As células utilizam quebra automática de texto e ajustam a altura da linha ao conteúdo. Nos campos de processos, servidor e clientes são exibidos em linhas separadas para facilitar a leitura.

## Recursos do Windows

A aba **Recursos do Windows** é preenchida nos modos **Seguro (recomendado)** e **Somente portas**. Para cada porta, ela apresenta:

- processo proprietário identificado;
- presença em faixa TCP excluída ou reservada pelo Windows;
- presença em faixa UDP excluída ou reservada;
- regras específicas de firewall relacionadas à porta;
- redirecionamentos configurados pelo recurso `portproxy`;
- situação dos privilégios administrativos da ferramenta.

As consultas são somente de leitura. A ferramenta não cria, remove ou altera regras do Windows.

Sem privilégio administrativo, algumas informações de firewall podem ficar indisponíveis. Essa limitação é mostrada na própria aba e não impede as demais verificações.

## Comparação antes e depois

A aba **Antes e depois** ajuda a confirmar o efeito de uma reinicialização ou alteração de configuração:

1. com o estado inicial ainda presente, clique em **Capturar estado anterior**;
2. reinicie ou reconfigure o aplicativo analisado;
3. clique em **Comparar estado atual**;
4. consulte as linhas destacadas na tabela.

A comparação considera porta, estados TCP e UDP, processos associados e diagnóstico. Linhas alteradas ficam amarelas; linhas sem alteração ficam verdes.

Essa comparação permanece apenas na sessão atual e também é incluída no relatório exportado.

## Conclusão em linguagem simples

O painel **Conclusão simples e próxima ação** transforma os resultados técnicos em uma orientação direta. Exemplos:

- portas livres aguardando configuração;
- transporte acessível, mas protocolo do serviço ainda não validado;
- canais reais conectados e teste funcional ainda necessário;
- conflito com outro processo;
- verificação cancelada;
- infraestrutura normal, mas operação funcional com falha.

As informações completas continuam disponíveis nas abas técnicas.

## Histórico da sessão

O histórico registra o resultado final de cada clique em **Verificar agora**, mesmo quando o diagnóstico não mudou. Durante o monitoramento automático, uma nova linha é adicionada somente quando muda:

- o estado de uma porta;
- o processo associado;
- o diagnóstico;
- a confirmação do teste funcional.

O histórico mantém até 100 registros na sessão atual. O botão **Limpar histórico da sessão** remove as linhas exibidas e permite que a próxima verificação registre novamente o estado atual. O histórico não é armazenado permanentemente quando a ferramenta é fechada.

Ao trocar o idioma, o histórico é reiniciado e o estado atual é registrado novamente no idioma selecionado.

## Exportar relatório

O botão **Exportar relatório**, disponível em **Detalhes técnicos**, cria um arquivo de texto contendo:

- data e hora;
- resumo das três camadas;
- portas analisadas;
- estados TCP e UDP;
- processos identificados;
- diagnósticos por porta;
- modo de diagnóstico utilizado;
- recursos do Windows encontrados;
- comparação antes e depois, quando realizada;
- pares sugeridos;
- histórico da sessão.

O relatório é criado no idioma selecionado na interface.

## Idiomas

O seletor no cabeçalho permite alternar imediatamente entre:

- **Português**, idioma padrão;
- **English**.

A tradução abrange controles, indicadores, mensagens, tabelas, diagnósticos e relatórios. O idioma escolhido é mantido para a próxima abertura.

## Configurações mantidas

Ao fechar normalmente a ferramenta, são salvos:

- porta 1;
- porta 2;
- início da pesquisa;
- diferença entre as portas;
- idioma selecionado.
- modo de diagnóstico selecionado.

O arquivo de preferências fica em:

```text
%APPDATA%\LocalChannelDoctor\settings.json
```

O histórico da sessão e os resultados temporários não são gravados nesse arquivo.

## Como interpretar resultados auxiliares

Exemplo de situação:

1. as duas portas aparecem conectadas;
2. um teste auxiliar aceita conexão ou devolve dados;
3. a operação real falha.

Nesse caso, a ferramenta não considera o sistema totalmente validado. Registre **Falhou** no terceiro indicador. Isso direciona a investigação para a camada da aplicação, sua configuração ou componentes que não são comprovados pelo teste de portas.

O inverso também pode ocorrer: um teste auxiliar pode não concluir a conexão enquanto o aplicativo real permanece conectado e operante. Nesse caso, a conexão real tem prioridade e o teste auxiliar aparece como **Inconclusivo**, nunca como prova isolada de falha.

## Fluxo recomendado de diagnóstico

1. Informe as mesmas portas configuradas no aplicativo.
2. Abra o aplicativo que utiliza os canais.
3. Clique em **Verificar agora**.
4. Analise as camadas de transporte e comunicação.
5. Execute uma operação real.
6. Registre **Funcionou** ou **Falhou**.
7. Se houver falha de porta ou conflito, procure um novo par.
8. Aplique o novo par no aplicativo e reinicie-o.
9. Repita a verificação segura.
10. Exporte o relatório quando precisar registrar ou compartilhar o diagnóstico.

## O que a ferramenta não comprova

A ferramenta não garante, sozinha:

- passagem de tráfego por equipamentos externos;
- funcionamento de hardware conectado;
- execução correta de todas as funções do aplicativo;
- disponibilidade futura de uma porta encontrada como livre;
- ausência de bloqueio em outro computador ou dispositivo de rede;
- correção automática da configuração do aplicativo.

Por isso, a validação final continua sendo o teste funcional realizado pelo usuário.

## Requisitos

- Windows;
- Windows PowerShell com os comandos de rede do sistema;
- permissões para consultar conexões e processos locais;
- componente de comunicação compatível apenas se o modo de teste ativo avançado for utilizado.

Se o componente de comunicação não estiver disponível, o modo seguro, a análise de portas e a observação das conexões reais continuam disponíveis.

## Solução de problemas

### A ferramenta informa porta livre, mas o aplicativo não funciona

Confirme se o aplicativo realmente foi reiniciado depois da alteração e execute **Verificar agora**. Porta livre significa apenas que ela estava disponível durante a pesquisa.

### TCP aparece conectado, mas o teste funcional falha

O transporte foi estabelecido, mas isso não garante a operação completa. Registre a falha funcional e exporte o relatório. Use o teste ativo avançado apenas como informação auxiliar.

### Uma porta não responde

No modo avançado, o teste é interrompido automaticamente após seis segundos. Uma ocorrência isolada é inconclusiva; sem conexão real observada, a falha só é confirmada após três ocorrências consecutivas. Verifique conflito, processo proprietário, configuração aplicada e se o serviço esperado está em execução.

Se a porta estiver completamente livre, o teste de resposta será ignorado e o resultado indicará que ela aguarda configuração; isso não é considerado falha.

### A interface parece estar usando valores antigos

Feche completamente a janela e abra novamente pelo atalho. Os valores salvos são carregados no início da nova sessão.

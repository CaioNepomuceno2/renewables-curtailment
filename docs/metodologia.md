# Metodologia

Este documento explica como cada número do README é calculado e como interpretá-lo. As escolhas de
tratamento de dados (campos, conferências, casos especiais) estão em [`dados.md`](dados.md); aqui o foco é
o raciocínio, as fórmulas e o desenho da validação.

## 1. Perguntas

1. Quanta energia eólica e solar deixou de ser gerada por restrição de operação do ONS (constrained-off)?
2. Onde (subsistema, UF, conjunto), por qual razão (REL, CNF, ENE) e em que horas?
3. Quanto essa energia vale para o sistema?
4. Dá para prever, na véspera, o corte total do dia seguinte melhor do que as regras simples?

## 2. Dados e período

| Fonte (ONS, dados abertos) | Granularidade | Período usado |
|---|---|---|
| Restrição por constrained-off, usinas eólicas (`restricao_coff_eolica_usi`) | semi-hora × usina/conjunto | out/2021 a out/2026 |
| Restrição por constrained-off, usinas fotovoltaicas (`restricao_coff_fotovoltaica`) | semi-hora × usina/conjunto | abr/2024 a out/2026 |
| CMO semi-horário por subsistema (`cmo-semi-horario`) | semi-hora × subsistema | 2021 a 2026 |
| Previsão × programado de eólicas e solares no PDP (`programacao_x_previsao`) | semi-hora × usina, diário | out/2024 a out/2026 |
| Carga global programada (API `apicarga.ons.org.br`) | semi-hora × área | out/2021 a out/2026 |

A base final tem 16,5 milhões de linhas semi-horárias. Os "últimos 12 meses" do README são nov/2025 a
out/2026.

## 3. Pipeline

| Etapa | Script | Função principal | Saída |
|---|---|---|---|
| Download e enxugamento | `scripts/01_download.py` | `ons.py` | `data/raw/*.parquet` |
| Energia cortada e valoração | `scripts/02_build.py` | `build.semihoraria`, `build.diario_usina` | `data/interim/coff_semihora.parquet` |
| Diagnóstico | `scripts/03_analyze.py` | `analise.py` | `reports/resumo*.csv`, `por_*.csv`, `top_conjuntos_12m.csv` |
| Previsor e baselines | `scripts/04_forecast.py` | `previsao.features`, `previsao.backtest`, `previsao.metricas` | `reports/previsao*.csv|json` |
| Figuras | `scripts/05_figures.py` | | `reports/figures/*.png` |
| Números de apoio à aplicação | `scripts/06_aplicacao.py` | | `reports/aplicacao.json` |

## 4. Definições e fórmulas

**Energia cortada (GNRa).** Para cada usina *u* e semi-hora *t* em que o ONS limitou a usina
(`val_geracaolimitada` não nulo):

```
corte[u,t] (MWh) = max(referência[u,t] − verificada[u,t], 0) × 0,5 h
```

Os valores do ONS estão em MWmed; multiplicar por 0,5 h dá a energia da meia hora. Fora das semi-horas
limitadas, a diferença entre referência e verificada **não** é corte (pode ser indisponibilidade da própria
usina ou erro da referência). Conferência: em ago/2025 (eólica) o cálculo e o campo apurado do ONS dão os
mesmos 3.138,9 GWh.

**Geração possível e % perdida.**

```
possível = verificada + cortada
% perdida = cortada / possível
```

Usar `verificada + cortada` evita depender da geração de referência fora das semi-horas limitadas.

**Valor pelo CMO.** Para cada semi-hora, `corte[u,t] × CMO[subsistema(u), t]`. O CMO é o custo marginal de
operação do sistema e fica baixo justamente quando sobra energia, então esse valor é um **piso** para o
sistema, não a perda de receita das usinas (que depende de contratos, PLD e ressarcimento). A régua "a cada
R$ 100/MWh, os 39,3 TWh valem R$ 3,9 bi" é só multiplicação, para quem quiser usar outro preço.

**Previsor do dia seguinte.** Alvo: corte total (eólica + solar) do SIN no dia D, em GWh. Modelo LightGBM
de regressão (`previsao.PARAMS`: 400 árvores, taxa 0,03, 15 folhas), média de 3 sementes.

Features (`previsao.features`):

| Grupo | Exemplos | Defasagem |
|---|---|---|
| Corte realizado | corte de D-1, D-2 e D-7, médias de 7 e 28 dias, máximo de 7 dias, corte por fonte e por razão | ≥ 1 dia (≥ 2 na variante estrita) |
| Fração cortada | corte / (corte + geração) nos últimos 7 dias | ≥ 1 dia |
| PDP de D | previsão, programado e corte programado de eólicas + solares | nenhuma: produzido em D-1 |
| Carga programada de D | por área e SIN, e razão contra 7 dias antes | nenhuma: produzida em D-1 |
| Calendário | dia da semana, feriado nacional, sazonalidade anual (seno e cosseno) | nenhuma |

**Métricas** (`previsao.metricas`):

* **Erro médio absoluto (MAE):** média de |real − previsto|, em GWh/dia.
* **Ganho sobre a persistência (skill):** `1 − MAE_modelo / MAE_persistência`. 35% quer dizer que o erro
  médio do modelo é 35% menor que o de "amanhã igual a hoje".
* **Viés:** `soma(previsto) / soma(real) − 1`. Negativo quer dizer que o modelo subestima o total.
* **Dia de corte alto:** dia com corte acima do 3º quartil do treino daquele mês.
* **AUC:** probabilidade de o modelo dar uma previsão maior a um dia de corte alto do que a um dia comum
  (0,5 = sorte; 1 = perfeito).
* **Precisão nos 20% de dias apontados:** dos 20% de dias com maior previsão, a fração que foi de fato dia de
  corte alto.

## 5. Desenho da validação

* **Fora da amostra, com origem móvel mensal:** para cada mês de jul/2025 a out/2026, o modelo é treinado só
  com dias anteriores ao primeiro dia do mês (treino desde out/2024, início do PDP aberto) e prevê os dias
  daquele mês. São 488 dias de teste, nenhum visto no treino.
* **O que é conhecido em D-1:** o corte realizado de D-1 é conhecido pelo ONS em tempo real, mas quem não é o
  ONS recebe os arquivos depois. Por isso há a variante **estrita**, que só usa realizados até D-2. Ela quase
  não perde desempenho (31,2 contra 30,6 GWh/dia), o que mostra que o resultado não depende de dados que um
  agente externo não teria.
* **Sem vazamento:** `tests/test_curtailment.py` verifica que nenhuma feature de realizados usa o próprio
  dia D e que os dados ex-ante entram só para o dia a que se referem.
* **Baselines:** persistência (o corte de ontem), média dos últimos 7 dias e o corte programado no PDP usado
  sozinho. Um modelo só vale a pena se bater esses três.

## 6. Premissas

| Premissa | Valor | Fonte / justificativa |
|---|---|---|
| Duração da semi-hora | 0,5 h | definição dos dados do ONS (MWmed por meia hora) |
| Semi-hora com corte | `val_geracaolimitada` não nulo | dicionário de dados do ONS |
| Preço para valoração | CMO do subsistema na mesma semi-hora | dataset `cmo-semi-horario` |
| Semi-horas sem CMO | valor zero (1,4% da energia em 12 meses) | conservador |
| Fonte no PDP | agregado eólica + solar | o nome da usina no PDP não identifica a fonte de forma confiável |
| Feriados | nacionais (`holidays.Brazil`) | feriados regionais não entram |

## 7. Sensibilidade

* **Preço:** a régua de R$ 3,9 bi a cada R$ 100/MWh permite trocar o CMO por qualquer outro preço.
* **Dados disponíveis em D-1:** realizados até D-1 (30,6 GWh/dia) ou até D-2 (31,2 GWh/dia).
* **Grupos de features:** só histórico (38,2), histórico + carga (39,8), histórico + carga + PDP (30,6). O
  ganho vem do PDP; a carga programada não ajuda.

## 8. Como interpretar

* **% perdida** é o indicador para comparar usinas e conjuntos de tamanhos diferentes; **GWh** é o
  indicador para dimensionar o problema e a receita em jogo.
* **Razão do corte** separa o que é problema de rede (REL, CNF: transmissão, confiabilidade) do que é sobra
  de energia no sistema (ENE). A resposta é diferente: reforço de rede no primeiro caso; armazenamento,
  flexibilidade e demanda no segundo.
* **O previsor ordena bem os dias e subestima o nível.** Para decidir *em que dias* agir (manutenção,
  cobertura), ele está pronto; para estimar *quanta energia* será cortada, recalibre o nível com os dados
  mais recentes.

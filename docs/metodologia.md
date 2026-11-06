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

**Calibração de nível** (`previsao.fator_calibracao` e `previsao.calibrar_nivel`). Para o mês *m*, com
`oos` as previsões fora da amostra dos dias anteriores (cada uma feita por um modelo treinado só com dias
anteriores a ela):

```
fator[m]        = soma(real) / soma(previsto)   nos 90 dias que terminam em (início de m) - k dias
previsto_cal[d] = fator[mês de d] x previsto[d]
```

com `k = 1` (D-1) ou `k = 2` (variante estrita). Razão de somas, e não média de razões, para que dias de
corte baixo não dominem. Mínimo de 60 dias na janela (senão o fator é 1) e trava em [0,5; 2,0]. O fator é
o mesmo em todos os dias do mês, então a ordem dos dias dentro do mês não muda. Os parâmetros foram fixados
antes de rodar a calibração no período de teste (ver README, "Calibração de nível").

**Métricas** (`previsao.metricas`):

* **Erro médio absoluto (MAE):** média de |real − previsto|, em GWh/dia.
* **Ganho sobre a persistência (skill):** `1 − MAE_modelo / MAE_persistência`. 35% quer dizer que o erro
  médio do modelo é 35% menor que o de "amanhã igual a hoje".
* **Viés:** `soma(previsto) / soma(real) − 1`. Negativo quer dizer que o modelo subestima o total.
* **WAPE:** `soma(|real − previsto|) / soma(real)`, o erro percentual ponderado pela energia.
* **MAPE:** média de |real − previsto| / real, só nos dias com corte real de pelo menos 10 GWh (abaixo disso o
  erro percentual explode); a coluna `mape_n_dias` diz quantos dias entram.
* **Spearman:** correlação entre os postos do real e do previsto nos dias de teste. Mede o quanto o modelo
  ordena bem os dias, sem depender do nível.
* **Dia de corte alto:** dia com corte acima do 3º quartil do treino daquele mês.
* **AUC:** probabilidade de o modelo dar uma previsão maior a um dia de corte alto do que a um dia comum
  (0,5 = sorte; 1 = perfeito).
* **Precisão nos 20% de dias apontados:** dos 20% de dias com maior previsão, a fração que foi de fato dia de
  corte alto.

## 5. Desenho da validação

* **Fora da amostra, com origem móvel mensal:** para cada mês de jul/2025 a out/2026, o modelo é treinado só
  com dias anteriores ao primeiro dia do mês (treino desde out/2024, início do PDP aberto) e prevê os dias
  daquele mês. São 488 dias de teste, nenhum visto no treino.
* **Aquecimento da calibração:** o mesmo backtest roda também de jan/2025 a jun/2025, só para haver
  previsões fora da amostra com que estimar o fator dos primeiros meses de teste. Esses meses não entram
  nas métricas, e as previsões de jul/2025 em diante são as mesmas com ou sem aquecimento.
* **Regra de aceite da calibração** (fixada antes de rodar no teste): o calibrado só vira o padrão se, nas
  duas variantes (D-1 e D-2), o viés cair em módulo, o Spearman e a AUC não caírem mais de 0,005 e o MAE não
  subir mais de 1 GWh/dia. A regra é avaliada em `scripts/04_forecast.py` e o resultado fica em
  `reports/previsao.json` (`calibracao.aceite`).
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
* **Janela do fator de calibração:** 60, 90 (padrão) e 180 dias dão viés de +2,7% a +3,4% e Spearman de
  0,855 a 0,866 no D-1, e nenhuma passa na regra de aceite
  (`reports/previsao_calibracao_sensibilidade.csv`). Uma janela de 30 dias não calibra nada com o mínimo de 60 dias.
* **Grupos de features:** só histórico (38,2), histórico + carga (39,8), histórico + carga + PDP (30,6). O
  ganho vem do PDP; a carga programada não ajuda.

## 8. Como interpretar

* **% perdida** é o indicador para comparar usinas e conjuntos de tamanhos diferentes; **GWh** é o
  indicador para dimensionar o problema e a receita em jogo.
* **Razão do corte** separa o que é problema de rede (REL, CNF: transmissão, confiabilidade) do que é sobra
  de energia no sistema (ENE). A resposta é diferente: reforço de rede no primeiro caso; armazenamento,
  flexibilidade e demanda no segundo.
* **O previsor ordena bem os dias e subestima o nível.** Para decidir *em que dias* agir (manutenção,
  cobertura), ele está pronto. Para estimar *quanta energia* será cortada, o nível do total ainda é incerto:
  o fator de nível tirou o viés do total (de -13,0% para +2,7%), mas custou ranqueamento (Spearman de 0,870
  para 0,863) e errou até 40% (49% no D-2) em meses de mudança de regime (README, seção 5). Use o calibrado só para o
  total, nunca para ordenar dias, e trate o total como ordem de grandeza.

## 9. Reprodução e revisão dos dados do ONS

Em 06/11/2026 o pipeline (`01` a `06`) foi rodado de novo, com o código da versão anterior e os dados
baixados nessa data, antes de qualquer mudança de método. O objetivo era separar o que mudou porque o ONS
revisa os dados do que mudou por causa da calibração de nível.

* **Só out/2026 foi revisado:** eólica de 2.581,4 para 2.671,9 GWh (+3,5%) e solar de 1.420,6 para
  1.448,4 GWh (+2,0%). Os outros 60 meses são idênticos.
* **As previsões de jul/2025 a set/2026 saem idênticas** (todas as colunas de `previsao_diaria.csv`); só as
  de out/2026 mudam, porque o realizado do mês mudou. Portanto não há deriva de bibliotecas.

| Número | Versão anterior do README | Regenerado em 06/11/2026 (código inalterado) |
|---|---|---|
| Corte nos últimos 12 meses | 39.145 GWh | 39.264 GWh (+0,30%) |
| % da geração possível perdida | 21,74% | 21,79% |
| Valor pelo CMO | R$ 2.493,9 milhões | R$ 2.494,9 milhões |
| Corte médio real no teste | 119,58 GWh/dia | 119,82 GWh/dia |
| Modelo completo: erro médio | 30,68 GWh/dia | 30,59 GWh/dia |
| Modelo completo: viés | -12,85% | -13,01% |
| Modelo completo: AUC | 0,9133 | 0,9134 |

Os números do README já usam a versão regenerada. O efeito da calibração (seção 5 do README) é medido
contra ela, sobre os mesmos dados: as colunas e linhas do modelo original em `reports/` não mudam com a
calibração, que só acrescenta colunas, linhas e arquivos.

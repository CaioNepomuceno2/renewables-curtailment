# Curtailment de eólicas e solares no Brasil

[![CI](https://github.com/CaioNepomuceno2/renewables-curtailment/actions/workflows/ci.yml/badge.svg)](https://github.com/CaioNepomuceno2/renewables-curtailment/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.11%2B-blue)
![Dados: ONS](https://img.shields.io/badge/dados-ONS%20(abertos)-0b7a75)

**Quanta energia renovável o Brasil está deixando de gerar por ordem do ONS, onde, por quê, quanto isso
vale e dá para prever o corte do dia seguinte?**

Este projeto responde com os dados abertos de **restrição de operação por constrained-off** do ONS:
cada meia hora de cada usina ou conjunto eólico (desde out/2021) e solar (desde abr/2024) supervisionado,
com a geração verificada, a geração de referência (o que a usina poderia gerar) e a razão do corte.

## Resultados

### 1. Um quinto da geração eólica e solar possível foi jogada fora

**Últimos 12 meses (nov/2025 a out/2026):**

| | Eólica | Solar | Total |
|---|---|---|---|
| Energia cortada | 26,4 TWh | 12,7 TWh | **39,1 TWh** |
| % da geração possível (verificada + cortada) | 20,3% | 25,4% | **21,7%** |
| Valor pelo CMO do subsistema no instante do corte | R$ 2,04 bi | R$ 0,45 bi | **R$ 2,49 bi** |

![evolução mensal](reports/figures/01_evolucao_mensal.png)

O corte era residual até 2022 (0,5% da eólica) e explodiu a partir do 2º semestre de 2023:

| Ano | Energia cortada | % perdida | Valor pelo CMO |
|---|---|---|---|
| 2022 | 0,4 TWh | 0,5% | R$ 0,01 bi |
| 2023 | 3,4 TWh | 3,6% | R$ 0,01 bi |
| 2024 (solar a partir de abr) | 12,8 TWh | 9,4% | R$ 0,96 bi |
| 2025 | 37,2 TWh | 20,7% | R$ 3,22 bi |
| 2026 (jan a out) | 33,3 TWh | 22,1% | R$ 1,67 bi |

### 2. Onde e por quê

![razão e UF](reports/figures/02_razao_uf.png)

* **69% do corte é por razão energética (ENE):** sobra energia no sistema, e o ONS manda reduzir. Outros
  21% são por **confiabilidade elétrica (CNF)** e só 10% por **indisponibilidade externa (REL)**, como falha
  ou limite da rede de transmissão.
* Pelo próprio dicionário de dados do ONS, **só o corte REL tem a geração de referência final enviada à CCEE**
  para apuração de encargos (ESS) por constrained-off. Ou seja, cerca de 90% da energia cortada nos últimos 12
  meses (ENE e CNF) fica fora desse caminho de ressarcimento.
* **O Nordeste concentra 82% do corte** (31,9 TWh). O Rio Grande do Norte sozinho perdeu 11,8 TWh de eólica,
  **31% da sua geração eólica possível**. Minas Gerais lidera a solar (4,9 TWh).
* Os conjuntos mais afetados perderam entre 12% e 49% da geração possível:

![top conjuntos](reports/figures/04_top_conjuntos.png)

### 3. Quando: o corte acompanha o sol, inclusive o da eólica

![perfil horário](reports/figures/03_perfil_horario.png)

O corte se concentra entre 7h e 15h. É quando a geração solar centralizada e a distribuída (dos telhados,
que o ONS não controla) reduzem a carga líquida, e o ONS corta tanto a solar quanto a eólica.

### 4. O corte do dia seguinte é previsível

Para cada dia D, o modelo (LightGBM) usa só o que se sabe no fim de D-1: o corte realizado até D-1, o
calendário, a carga programada para D e a **previsão x programado de eólicas e solares do PDP** (programação
diária do ONS feita em D-1). O teste é fora da amostra, com origem móvel mensal: cada mês é previsto por
um modelo treinado só com os dias anteriores. São 488 dias (jul/2025 a out/2026), com corte médio real de
120 GWh/dia.

![previsão](reports/figures/05_previsao.png)

| Método (jul/2025 a out/2026) | Erro médio (GWh/dia) | Ganho sobre a persistência | AUC "dia de corte alto" |
|---|---|---|---|
| **Modelo com informação de D-1** | **30,7** | **34%** | **0,91** |
| Persistência (o corte de ontem) | 46,8 | - | 0,83 |
| Média dos últimos 7 dias | 53,5 | -14% | 0,74 |
| Corte programado no PDP, sozinho | 74,4 | -59% | 0,91 |
| Modelo estrito, com realizados só até D-2 | 31,3 | 50% (sobre a persistência de D-2, 63,3) | 0,91 |

* **O PDP subestima o corte em 62%:** a programação do dia anterior prevê só 38% da energia que acaba
  sendo cortada em tempo real. Mas ela ordena bem os dias (AUC de 0,91), e por isso é a variável mais
  importante do modelo (57% do ganho), que aprende a corrigir a escala.
* Nos 20% de dias com maior previsão, 95% foram de fato dias de corte alto (acima do 3º quartil do treino),
  contra 45% na média.
* A carga programada quase não ajuda (39,7 contra 38,1 GWh/dia sem o PDP); o que carrega informação é o
  próprio programa de eólicas e solares.

## Decisões de dados

Detalhes em [`docs/dados.md`](docs/dados.md). As principais:

* **Energia cortada = geração não realizada apurada (GNRa) do ONS:** `max(referência - verificada, 0)`, só
  nos patamares em que o ONS limitou a usina (`val_geracaolimitada` não nulo), em MWmed x 0,5 h. Recalculo
  a partir das colunas brutas porque os arquivos antigos não trazem o campo apurado; em ago/2025 os dois
  dão exatamente 3.138,9 GWh.
* **% perdida = cortada / (verificada + cortada)**, a geração possível, sem depender de referência fora dos
  patamares limitados.
* **Valoração pelo CMO**, o custo marginal de operação semi-horário do subsistema no instante do corte. É
  o valor da energia para o sistema, e é baixo justamente quando sobra energia: a média ponderada nos últimos
  12 meses foi de R$ 64,6/MWh, e em 2023 ficou perto de zero. Não é a perda de receita das usinas, que depende
  de contratos e do ressarcimento. Como régua: **a cada R$ 100/MWh, os 39,1 TWh valem R$ 3,9 bi.** Faltou
  CMO para 1,4% da energia cortada nos últimos 12 meses, que entrou com valor zero.
* **Previsão x programado do PDP:** o nome da usina no PDP não identifica a fonte de forma confiável, então
  uso o agregado eólica + solar. O corte programado é a soma de `max(previsão - programado, 0)` por usina.

## Estrutura

```
src/curtailment/
  ons.py        download (CKAN + S3 do ONS, API de carga programada), arquivos enxutos em parquet
  build.py      energia cortada e valoração pelo CMO (base semi-horária por usina)
  analise.py    totais por ano, fonte, razão, UF, subsistema, conjunto e hora
  previsao.py   features com corte temporal, backtest de origem móvel, baselines e métricas
scripts/        01_download, 02_build, 03_analyze, 04_forecast, 05_figures
reports/        tabelas (CSV/JSON) e figuras
tests/          energia cortada, valoração, agregação do PDP e ausência de vazamento nas features
```

## Reproduzir

```bash
pip install -e ".[dev]"
python scripts/01_download.py   # ~200 MB do ONS (cerca de 10 min)
python scripts/02_build.py      # base semi-horária: 16,5 milhões de linhas
python scripts/03_analyze.py
python scripts/04_forecast.py
python scripts/05_figures.py
pytest
```

## Limitações

* **Cobertura:** só usinas e conjuntos supervisionados pelo ONS nas modalidades Tipo I, II-B e II-C. A
  geração distribuída (telhados) não entra, e a série solar só começa em abr/2024.
* **Geração de referência é uma estimativa** do próprio ONS (RO-AO.BR.13). Se ela estiver alta, o corte
  também está.
* **Valoração pelo CMO é um piso para o sistema**, não a perda financeira das usinas.
* **Disponibilidade dos dados para quem não é o ONS:** o corte realizado de D-1 e o PDP existem em D-1 na
  operação, mas os arquivos abertos são publicados depois. Por isso a variante estrita usa realizados só até
  D-2. Ela quase não perde desempenho (31,3 contra 30,7 GWh/dia).
* **O modelo subestima o total** (viés de -13% no período de teste), porque o corte cresceu ao longo do
  período e árvores de decisão não extrapolam tendência. Para ranquear dias isso pesa pouco; para estimar
  energia, vale calibrar o nível.
* **Treino curto:** o PDP aberto só existe desde out/2024, então o primeiro mês de teste foi previsto com
  9 meses de histórico.

---
Autor: **Caio Nepomuceno**. Dados públicos do ONS. Projeto independente, sem vínculo com o ONS, a CCEE ou
agentes de geração.

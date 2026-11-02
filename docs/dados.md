# Decisões de dados

Registro das escolhas de tratamento que afetam os resultados.

## Fontes (ONS, dados abertos)

| Dataset (CKAN `dados.ons.org.br`) | Uso | Período |
|---|---|---|
| `restricao_coff_eolica_usi` | apuração semi-horária de constrained-off por usina/conjunto eólico | out/2021 a out/2026 |
| `restricao_coff_fotovoltaica` | idem, usinas/conjuntos fotovoltaicos | abr/2024 a out/2026 |
| `cmo-semi-horario` | CMO por subsistema para valorar a energia cortada | 2021 a 2026 |
| `programacao_x_previsao` | previsão x programado de eólicas e solares no PDP (diário) | out/2024 a out/2026 |
| API `apicarga.ons.org.br/prd/cargaprogramada` | carga global programada por área (N, NE, S, SECO) | out/2021 a out/2026 |

Os arquivos mensais são baixados em parquet e regravados só com as colunas usadas (`ons.COLS_COFF`).
Os conjuntos (`ceg = "-"`) e as usinas isoladas aparecem uma vez cada no dataset `_usi`, sem duplicidade:
cada `id_ons` tem exatamente 48 patamares por dia. O dataset `_detail` (por usina dentro do conjunto) não é
usado, para não contar a mesma energia duas vezes.

## Energia cortada

O dicionário define a **Geração Não Realizada Apurada (GNRa)** como a diferença entre a geração de referência
e a verificada (mínimo zero), **só nos períodos com limitação**. Um patamar é limitado quando
`val_geracaolimitada` não é nulo (zero significa ordem de desligar). A energia em MWh é o valor em MWmed x 0,5.

* Recalculado a partir das colunas brutas (`build.energia_cortada_mwh` e `build.semihoraria`), porque nem
  todos os arquivos trazem `val_geracaonaorealizadaapurada`. Conferência em ago/2025 (eólica): 3.138,9 GWh no
  cálculo e no campo apurado.
* Fora dos patamares limitados a diferença referência - verificada **não** é corte (pode ser erro da própria
  referência ou indisponibilidade da usina).
* Patamares limitados sem razão informada viram `NI`; somam menos de 1 MWh nos últimos 12 meses.

## Razões

`REL`: indisponibilidade externa (elétrica). `CNF`: confiabilidade elétrica. `ENE`: razão energética.
`PAR`: restrição do parecer de acesso. A geração de referência **final** só é calculada para `REL` e é a
enviada à CCEE para os encargos de serviço do sistema (ESS) por constrained-off.

## Valoração

`corte_mwh x CMO` do subsistema no mesmo `din_instante`. O CMO vem como texto em alguns anos e é convertido.
Faltou CMO para 1,4% da energia cortada nos últimos 12 meses; essas meias horas entram com valor zero.

## Previsão

* Alvo: energia cortada total (eólica + solar) do SIN no dia D, em GWh.
* Realizados (corte, geração, corte por razão e por fonte) só entram com defasagem >= 1 dia (>= 2 na variante
  estrita). Dados ex-ante de D (carga programada, previsão e programado do PDP) entram sem defasagem, porque
  são produzidos na programação diária em D-1. `tests/test_curtailment.py` verifica as duas regras.
* Backtest com origem móvel mensal de jul/2025 a out/2026, treino desde out/2024 (início do PDP aberto),
  média de 3 sementes.
* "Dia de corte alto": acima do 3º quartil do alvo no treino de cada mês.

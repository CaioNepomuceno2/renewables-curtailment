"""Backtest do previsor de corte do dia seguinte contra baselines (jul/2025 a out/2026)."""
import json

import duckdb
import lightgbm as lgb
import numpy as np
import pandas as pd

from curtailment import INTERIM, RAW, REPORTS
from curtailment import previsao as P

INICIO_TREINO, INICIO_TESTE, FIM_TESTE = "2024-10-01", "2025-07-01", "2026-10-31"

diario = pd.read_parquet(INTERIM / "diario_total.parquet")
diario["dia"] = pd.to_datetime(diario.dia).dt.date.astype(str)
razao = duckdb.sql(f"""
    select cast(cast(instante as date) as varchar) as dia, razao, sum(corte_mwh) / 1e3 as corte_gwh
    from read_parquet('{INTERIM / "coff_semihora.parquet"}') where razao is not null group by all
""").df()

carga = pd.read_parquet(RAW / "carga_programada.parquet")
carga = (carga.groupby(["dat_referencia", "cod_areacarga"]).val_cargaglobalprogramada.mean()
         .unstack().rename(columns=lambda a: f"carga_{a.lower()}_mw"))
carga["carga_sin_mw"] = carga.sum(axis=1)
carga.index = carga.index.astype(str)

pxp = pd.read_parquet(RAW / "programacao_x_previsao.parquet")
pxp = pxp.groupby("dia")[["val_previsao", "val_programado", "corte_programado"]].sum() * 0.5 / 1e3
pxp.columns = ["pxp_previsao_gwh", "pxp_programado_gwh", "pxp_corte_programado_gwh"]
pxp["pxp_frac_corte_programado"] = pxp.pxp_corte_programado_gwh / pxp.pxp_previsao_gwh

t = P.tabela_diaria(diario, razao, carga, pxp)
t = t[t.index >= "2024-04-01"]  # a série solar começa em abr/2024


def _cols(k: int, grupos: tuple[str, ...]) -> list[str]:
    cols = P.features(t, k).columns
    exante = {"carga": [c for c in cols if c.startswith("carga_")], "pxp": [c for c in cols if c.startswith("pxp_")]}
    base = [c for c in cols if not (c.startswith(("carga_", "pxp_")))]
    return base + [c for g in grupos for c in exante[g]]


configs = {
    "historico": (_cols(1, ()), 1),
    "historico_carga": (_cols(1, ("carga",)), 1),
    "completo": (_cols(1, ("carga", "pxp")), 1),
    "completo_estrito_D-2": (_cols(2, ("carga", "pxp")), 2),
}
cols_full = configs["completo"][0]

linhas, prev_all = [], {}
for nome, (cols, k) in configs.items():
    preds = [P.backtest(t, cols, INICIO_TESTE, FIM_TESTE, INICIO_TREINO, k, seed=s) for s in range(3)]
    p = preds[0].copy()
    p["pred"] = np.mean([x.pred for x in preds], axis=0)
    b = P.baselines(t, p.index, k)
    ref = P.metricas(p.real, b.persistencia)["mae_gwh"]
    linhas.append({"metodo": f"modelo_{nome}", "defasagem_min": k,
                   **P.metricas(p.real, p.pred, ref, p.limiar_alto)})
    for bn in b.columns:
        linhas.append({"metodo": f"baseline_{bn}", "defasagem_min": k,
                       **P.metricas(p.real, b[bn], ref, p.limiar_alto)})
    prev_all[nome] = p.pred
    if k == 1:
        p_base = p

# o próprio programa do ONS como previsão: corte programado (previsão - programado) para o dia
prog = t.loc[p_base.index, "pxp_corte_programado_gwh"]
ref1 = P.metricas(p_base.real, P.baselines(t, p_base.index, 1).persistencia)["mae_gwh"]
linhas.append({"metodo": "baseline_corte_programado_pdp", "defasagem_min": 0,
               **P.metricas(p_base.real, prog, ref1, p_base.limiar_alto)})

met = pd.DataFrame(linhas).drop_duplicates(["metodo", "defasagem_min"])
met.round(4).to_csv(REPORTS / "previsao_metricas.csv", index=False)
print(met.round(3).to_string(index=False))

saida = pd.DataFrame({"real_gwh": p_base.real, **{f"pred_{k}": v for k, v in prev_all.items()},
                      "persistencia": P.baselines(t, p_base.index, 1).persistencia,
                      "corte_programado_pdp": prog})
saida.round(3).to_csv(REPORTS / "previsao_diaria.csv", index_label="dia")

# importância das features no modelo completo treinado com tudo até o fim do teste
F = P.features(t, 1)[cols_full]
ok = (F.index >= INICIO_TREINO) & t[P.ALVO].notna()
m = lgb.LGBMRegressor(random_state=0, importance_type="gain", **P.PARAMS).fit(F[ok], t.loc[ok, P.ALVO])
imp = pd.Series(m.feature_importances_, index=cols_full).sort_values(ascending=False)
(imp / imp.sum()).round(4).rename("ganho_relativo").to_csv(REPORTS / "previsao_importancia.csv")
print((imp / imp.sum()).head(12).round(3).to_string())

(REPORTS / "previsao.json").write_text(json.dumps({
    "periodo_teste": [INICIO_TESTE, FIM_TESTE], "inicio_treino": INICIO_TREINO,
    "n_dias_teste": len(p_base), "corte_medio_real_gwh_dia": round(float(p_base.real.mean()), 2),
    "metricas": met.round(4).to_dict("records")}, indent=2, ensure_ascii=False), encoding="utf-8")

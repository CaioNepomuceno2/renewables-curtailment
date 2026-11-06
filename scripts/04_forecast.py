"""Backtest do previsor de corte do dia seguinte contra baselines (jul/2025 a out/2026).

Além do modelo original, avalia a versão com calibração de nível (``previsao.calibrar_nivel``): o fator
sai só de previsões fora da amostra de dias anteriores a cada mês. Para ter essas previsões já no primeiro
mês de teste, o backtest roda desde ``INICIO_CALIB`` (aquecimento); as métricas usam só o período de teste.
"""
import json

import duckdb
import lightgbm as lgb
import numpy as np
import pandas as pd

from curtailment import INTERIM, RAW, REPORTS
from curtailment import previsao as P

INICIO_TREINO, INICIO_TESTE, FIM_TESTE = "2024-10-01", "2025-07-01", "2026-10-31"
INICIO_CALIB = "2025-01-01"  # aquecimento: previsões fora da amostra só para estimar o fator; fora das métricas
CALIBRADOS = ("completo", "completo_estrito_D-2")
# só mostra robustez; o padrão é P.JANELA_CALIBRACAO. Uma janela de 30 dias fica de fora: com o mínimo de
# 60 dias o fator seria sempre 1 (retirada depois de ver que a linha era idêntica ao modelo original)
JANELAS_SENSIBILIDADE = (60, 90, 180)
# regra de aceite (previsao.regra_de_aceite), fixada antes de ver o teste: o calibrado vira o padrão para
# estimar energia se o viés cair, o ranqueamento não piorar (Spearman e AUC com queda de no máximo 0,005) e o
# MAE não subir mais de 1 GWh/dia, nas duas variantes (D-1 e D-2)



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

linhas, prev_all, oos_all, cal_all = [], {}, {}, {}
for nome, (cols, k) in configs.items():
    # previsões fora da amostra desde o aquecimento; cada mês é previsto só com dias anteriores a ele
    preds = [P.backtest(t, cols, INICIO_CALIB, FIM_TESTE, INICIO_TREINO, k, seed=s) for s in range(3)]
    oos = preds[0].copy()
    oos["pred"] = np.mean([x.pred for x in preds], axis=0)
    oos_all[nome] = oos
    p = oos.loc[INICIO_TESTE:].copy()
    b = P.baselines(t, p.index, k)
    ref = P.metricas(p.real, b.persistencia)["mae_gwh"]
    linhas.append({"metodo": f"modelo_{nome}", "defasagem_min": k,
                   **P.metricas(p.real, p.pred, ref, p.limiar_alto)})
    for bn in b.columns:
        linhas.append({"metodo": f"baseline_{bn}", "defasagem_min": k,
                       **P.metricas(p.real, b[bn], ref, p.limiar_alto)})
    if nome in CALIBRADOS:
        cal = P.calibrar_nivel(oos, INICIO_TESTE, FIM_TESTE, k)
        cal_all[nome] = cal
        linhas.append({"metodo": f"modelo_{nome}_calibrado", "defasagem_min": k,
                       **P.metricas(p.real, cal.pred_cal, ref, p.limiar_alto)})
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
for nome, cal in cal_all.items():
    saida[f"pred_{nome}_cal"] = cal.pred_cal
    saida[f"fator_{nome}"] = cal.fator
saida.round(3).to_csv(REPORTS / "previsao_diaria.csv", index_label="dia")

# intermediário em disco: previsões fora da amostra (aquecimento + teste) de todos os modelos
pd.concat({n: o for n, o in oos_all.items()}, names=["modelo", "dia"]).reset_index().to_parquet(
    INTERIM / "previsoes_oos.parquet", index=False)

# diagnóstico mensal da calibração (modelo completo): o corte cresce e o fator acompanha
mensal = []
for nome in CALIBRADOS:
    cal, p = cal_all[nome], oos_all[nome].loc[INICIO_TESTE:]
    g = pd.DataFrame({"real": p.real, "pred": cal.pred, "pred_cal": cal.pred_cal, "fator": cal.fator}
                     ).groupby(lambda d: d.strftime("%Y-%m"))
    por_mes = g.agg(real_gwh=("real", "sum"), pred_gwh=("pred", "sum"), pred_cal_gwh=("pred_cal", "sum"),
                    fator=("fator", "first"))
    por_mes["vies_pct"] = 100 * (por_mes.pred_gwh / por_mes.real_gwh - 1)
    por_mes["vies_cal_pct"] = 100 * (por_mes.pred_cal_gwh / por_mes.real_gwh - 1)
    por_mes.insert(0, "modelo", nome)
    mensal.append(por_mes.reset_index(names="mes"))
mensal = pd.concat(mensal)
mensal.round(4).to_csv(REPORTS / "previsao_calibracao_mensal.csv", index=False)
# diagnóstico escrito depois de ver o resultado: não faz parte da regra de aceite
diag_mensal = {}
for nome, g in mensal.groupby("modelo"):
    diag_mensal[nome] = {
        "erro_mensal_medio_pct_original": round(float(g.vies_pct.abs().mean()), 2),
        "erro_mensal_medio_pct_calibrado": round(float(g.vies_cal_pct.abs().mean()), 2),
        "pior_mes_pct_original": round(float(g.vies_pct.iloc[g.vies_pct.abs().argmax()]), 2),
        "pior_mes_pct_calibrado": round(float(g.vies_cal_pct.iloc[g.vies_cal_pct.abs().argmax()]), 2),
        "meses_com_erro_acima_de_20pct_original": int((g.vies_pct.abs() > 20).sum()),
        "meses_com_erro_acima_de_20pct_calibrado": int((g.vies_cal_pct.abs() > 20).sum()),
        "n_meses": len(g)}

# sensibilidade à janela do fator (o padrão fica em P.JANELA_CALIBRACAO, fixado antes do teste)
sens = []
for nome in CALIBRADOS:
    k, p = configs[nome][1], oos_all[nome].loc[INICIO_TESTE:]
    ref = P.metricas(p.real, P.baselines(t, p.index, k).persistencia)["mae_gwh"]
    for w in JANELAS_SENSIBILIDADE:
        cal = P.calibrar_nivel(oos_all[nome], INICIO_TESTE, FIM_TESTE, k, janela=w)
        mc = P.metricas(p.real, cal.pred_cal, ref, p.limiar_alto)
        sens.append({"modelo": nome, "janela_dias": w, "padrao": w == P.JANELA_CALIBRACAO, **mc,
                     "passa_regra_de_aceite": P.regra_de_aceite(met.set_index("metodo").loc[f"modelo_{nome}"], mc)["aceito"]})
pd.DataFrame(sens).round(4).to_csv(REPORTS / "previsao_calibracao_sensibilidade.csv", index=False)

# regra de aceite
mm = met.set_index("metodo")
aceite = {}
for nome in CALIBRADOS:
    aceite[nome] = P.regra_de_aceite(mm.loc[f"modelo_{nome}"], mm.loc[f"modelo_{nome}_calibrado"])
print(json.dumps(aceite, indent=1))

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
    "calibracao": {"inicio_aquecimento": INICIO_CALIB, "janela_dias": P.JANELA_CALIBRACAO,
                   "min_dias": P.MIN_DIAS_CALIBRACAO, "limites_fator": list(P.LIMITES_FATOR),
                   "piso_mape_gwh": P.PISO_MAPE_GWH, "tolerancia_ranqueamento": P.TOL_RANQUEAMENTO,
                   "tolerancia_mae_gwh": P.TOL_MAE_GWH, "aceite": aceite,
                   "diagnostico_mensal_posterior_ao_resultado": diag_mensal},
    "metricas": met.round(4).to_dict("records")}, indent=2, ensure_ascii=False), encoding="utf-8")

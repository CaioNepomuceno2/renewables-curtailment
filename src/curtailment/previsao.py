"""Previsão do corte total do dia seguinte (eólica + solar, SIN).

Enquadramento: no fim do dia D-1, prever a energia cortada no dia D (GWh). Duas famílias de informação:

* **realizada** (corte e geração verificados): entra só até D-1 (``shift`` >= 1). O ONS conhece o corte
  de D-1 em tempo real; quem não é o ONS recebe esses dados depois, por isso também há uma variante
  ``estrita`` que só usa realizados até D-2;
* **ex-ante para D** (carga programada e previsão x programado de eólicas/solares do PDP): é produzida na
  programação diária feita em D-1 para o dia D, então pode entrar sem defasagem.
"""
from __future__ import annotations

from dataclasses import dataclass

import holidays
import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score

ALVO = "corte_gwh"


def tabela_diaria(diario: pd.DataFrame, razao: pd.DataFrame, carga: pd.DataFrame | None,
                  pxp: pd.DataFrame | None) -> pd.DataFrame:
    """Uma linha por dia com o alvo e as séries brutas (sem defasagem ainda)."""
    d = diario.pivot_table(index="dia", columns="fonte", values=["corte_gwh", "geracao_gwh"], aggfunc="sum")
    d.columns = [f"{a}_{b}" for a, b in d.columns]
    d = d.fillna(0)
    d[ALVO] = d.filter(like="corte_gwh_").sum(axis=1)
    d["geracao_gwh"] = d.filter(like="geracao_gwh_").sum(axis=1)
    r = razao.pivot_table(index="dia", columns="razao", values="corte_gwh", aggfunc="sum").fillna(0)
    d = d.join(r.add_prefix("corte_razao_"), how="left")
    if carga is not None:
        d = d.join(carga, how="left")
    if pxp is not None:
        d = d.join(pxp, how="left")
    d.index = pd.to_datetime(d.index)
    return d.sort_index()


def features(t: pd.DataFrame, defasagem_min: int = 1) -> pd.DataFrame:
    """Features do dia D usando realizados até D-``defasagem_min`` e dados ex-ante de D."""
    f = pd.DataFrame(index=t.index)
    k = defasagem_min
    y = t[ALVO]
    for lag in sorted({k, k + 1, 7}):
        f[f"corte_lag{lag}"] = y.shift(lag)
    f["corte_media7"] = y.shift(k).rolling(7).mean()
    f["corte_media28"] = y.shift(k).rolling(28).mean()
    f["corte_max7"] = y.shift(k).rolling(7).max()
    for c in [c for c in t.columns if c.startswith(("corte_gwh_", "corte_razao_"))]:
        f[f"{c}_lag{k}"] = t[c].shift(k)
        f[f"{c}_media7"] = t[c].shift(k).rolling(7).mean()
    f["frac_corte_media7"] = (y.shift(k).rolling(7).sum()
                              / (y + t["geracao_gwh"]).shift(k).rolling(7).sum())
    # ex-ante para o dia D (programação diária feita em D-1)
    for c in [c for c in t.columns if c.startswith(("carga_", "pxp_"))]:
        f[c] = t[c]
    if "carga_sin_mw" in t:
        f["carga_sin_vs_7d"] = t["carga_sin_mw"] / t["carga_sin_mw"].shift(7)
    # calendário
    feriados = holidays.Brazil(years=range(t.index.min().year, t.index.max().year + 1))
    f["dia_semana"] = t.index.day_of_week
    f["feriado"] = [int(d in feriados) for d in t.index.date]
    doy = t.index.day_of_year
    f["sazonal_sin"], f["sazonal_cos"] = np.sin(2 * np.pi * doy / 365.25), np.cos(2 * np.pi * doy / 365.25)
    return f


PARAMS = {"objective": "regression", "learning_rate": 0.03, "n_estimators": 400, "num_leaves": 15,
              "min_child_samples": 10, "subsample": 0.8, "subsample_freq": 1, "colsample_bytree": 0.8,
              "reg_lambda": 1.0, "n_jobs": 1, "verbose": -1}


@dataclass
class Resultado:
    previsoes: pd.DataFrame
    metricas: pd.DataFrame


def backtest(t: pd.DataFrame, cols: list[str], inicio_teste: str, fim_teste: str,
             inicio_treino: str, defasagem_min: int = 1, seed: int = 0) -> pd.DataFrame:
    """Origem móvel mensal: para cada mês de teste, treina com todos os dias anteriores ao mês."""
    F = features(t, defasagem_min)[cols]
    y = t[ALVO]
    meses = pd.period_range(inicio_teste, fim_teste, freq="M")
    out = []
    for m in meses:
        ini, fim = m.start_time, m.end_time
        tr = (F.index >= inicio_treino) & (F.index < ini) & y.notna()
        te = (F.index >= ini) & (F.index <= fim)
        if te.sum() == 0:
            continue
        mdl = lgb.LGBMRegressor(random_state=seed, **PARAMS).fit(F[tr], y[tr])
        p = pd.DataFrame({"real": y[te], "pred": np.clip(mdl.predict(F[te]), 0, None)}, index=F.index[te])
        p["limiar_alto"] = np.nanpercentile(y[tr], 75)
        out.append(p)
    return pd.concat(out)


def baselines(t: pd.DataFrame, idx: pd.Index, defasagem_min: int = 1) -> pd.DataFrame:
    y = t[ALVO]
    return pd.DataFrame({"persistencia": y.shift(defasagem_min),
                         "media7": y.shift(defasagem_min).rolling(7).mean()}).loc[idx]


def metricas(real: pd.Series, pred: pd.Series, ref_mae: float | None = None,
             limiar: pd.Series | None = None) -> dict:
    ok = real.notna() & pred.notna()
    real, pred = real[ok], pred[ok]
    mae = float(np.mean(np.abs(real - pred)))
    out = {"mae_gwh": mae, "rmse_gwh": float(np.sqrt(np.mean((real - pred) ** 2))),
           "vies_pct": float(100 * (pred.sum() / real.sum() - 1)),
           "correlacao": float(np.corrcoef(real, pred)[0, 1])}
    if ref_mae is not None:
        out["skill_vs_persistencia"] = 1 - mae / ref_mae
    if limiar is not None:
        alto = (real > limiar[ok]).astype(int)
        if 0 < alto.sum() < len(alto):
            out["auc_dia_alto"] = float(roc_auc_score(alto, pred))
            k = max(1, round(0.2 * len(pred)))
            top = pred.sort_values(ascending=False).index[:k]
            out["precisao_top20pct"] = float(alto.loc[top].mean())
            out["taxa_dias_altos"] = float(alto.mean())
    return out

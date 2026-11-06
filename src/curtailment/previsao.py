"""Previsão do corte total do dia seguinte (eólica + solar, SIN).

Enquadramento: no fim do dia D-1, prever a energia cortada no dia D (GWh). Duas famílias de informação:

* **realizada** (corte e geração verificados): entra só até D-1 (``shift`` >= 1). O ONS conhece o corte
  de D-1 em tempo real; quem não é o ONS recebe esses dados depois, por isso também há uma variante
  ``estrita`` que só usa realizados até D-2;
* **ex-ante para D** (carga programada e previsão x programado de eólicas/solares do PDP): é produzida na
  programação diária feita em D-1 para o dia D, então pode entrar sem defasagem.

Calibração de nível (``fator_calibracao`` e ``calibrar_nivel``): árvores de decisão não extrapolam a
tendência, então o modelo subestima o total quando o corte cresce. O fator corrige o nível sem mexer na
ordem dos dias e usa só previsões fora da amostra de dias anteriores ao mês previsto.
"""
from __future__ import annotations

from dataclasses import dataclass

import holidays
import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score

ALVO = "corte_gwh"

# Calibração de nível. Valores fixados antes de olhar o período de teste (ver README, "Calibração de nível").
JANELA_CALIBRACAO = 90         # dias de previsões fora da amostra usados para estimar o fator
MIN_DIAS_CALIBRACAO = 60       # com menos dias que isso o fator é 1 (sem calibrar)
LIMITES_FATOR = (0.5, 2.0)     # trava de segurança; não é ajustada
PISO_MAPE_GWH = 10.0           # o MAPE ignora dias com corte real abaixo disso (o erro % explode)
TOL_RANQUEAMENTO = 0.005       # queda máxima aceita no Spearman e na AUC do calibrado
TOL_MAE_GWH = 1.0              # alta máxima aceita no MAE (GWh/dia) do calibrado


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
             inicio_treino: str, defasagem_min: int = 1, seed: int = 0,
             params: dict | None = None) -> pd.DataFrame:
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
        mdl = lgb.LGBMRegressor(random_state=seed, **(params or PARAMS)).fit(F[tr], y[tr])
        p = pd.DataFrame({"real": y[te], "pred": np.clip(mdl.predict(F[te]), 0, None)}, index=F.index[te])
        p["limiar_alto"] = np.nanpercentile(y[tr], 75)
        out.append(p)
    return pd.concat(out)


def fator_calibracao(oos: pd.DataFrame, ultimo_dia, janela: int = JANELA_CALIBRACAO,
                     min_dias: int = MIN_DIAS_CALIBRACAO, limites: tuple[float, float] = LIMITES_FATOR) -> float:
    """Fator de nível: soma(real) / soma(previsto) nos ``janela`` dias que terminam em ``ultimo_dia``.

    ``oos`` tem uma linha por dia (índice = dia) com as colunas ``real`` e ``pred``, onde ``pred`` é a
    previsão feita **fora da amostra** (por um modelo treinado só com dias anteriores àquele dia). Só
    entram dias até ``ultimo_dia`` (inclusive). Razão de somas, e não média de razões, para que dias de
    corte baixo não dominem. Com menos de ``min_dias`` dias na janela o fator é 1 (sem calibrar).
    """
    ultimo = pd.Timestamp(ultimo_dia)
    j = oos.loc[(oos.index > ultimo - pd.Timedelta(days=janela)) & (oos.index <= ultimo), ["real", "pred"]]
    j = j.dropna()
    if len(j) < min_dias or j.pred.sum() <= 0:
        return 1.0
    return float(np.clip(j.real.sum() / j.pred.sum(), *limites))


def calibrar_nivel(oos: pd.DataFrame, inicio: str, fim: str, defasagem_min: int = 1,
                   janela: int = JANELA_CALIBRACAO, min_dias: int = MIN_DIAS_CALIBRACAO,
                   limites: tuple[float, float] = LIMITES_FATOR) -> pd.DataFrame:
    """Aplica o fator de nível mês a mês às previsões de ``inicio`` a ``fim``.

    O fator de cada mês usa só dias até ``primeiro dia do mês - defasagem_min`` (D-1, ou D-2 na variante
    estrita), que é o que já se conhece quando o mês começa. O fator é o mesmo em todos os dias do mês,
    então a ordem dos dias dentro do mês não muda. Devolve ``pred`` (original), ``fator`` e ``pred_cal``.
    """
    alvo = oos.loc[inicio:fim, ["pred"]].copy()
    alvo["fator"] = 1.0
    for m in pd.period_range(inicio, fim, freq="M"):
        ultimo = m.start_time - pd.Timedelta(days=defasagem_min)
        no_mes = (alvo.index >= m.start_time) & (alvo.index <= m.end_time)
        alvo.loc[no_mes, "fator"] = fator_calibracao(oos, ultimo, janela, min_dias, limites)
    alvo["pred_cal"] = alvo.pred * alvo.fator
    return alvo


def regra_de_aceite(orig, cal, tol_ranqueamento: float = TOL_RANQUEAMENTO, tol_mae_gwh: float = TOL_MAE_GWH) -> dict:
    """Regra fixada antes de rodar a calibração no teste: o calibrado só vira o padrão se o viés cair em
    módulo, o ranqueamento (Spearman e AUC) não piorar além da tolerância e o MAE não subir além dela.

    ``orig`` e ``cal`` são as métricas (``metricas``) do modelo original e do calibrado.
    """
    r = {"vies_cai": bool(abs(cal["vies_pct"]) < abs(orig["vies_pct"])),
         "spearman_nao_piora": bool(cal["spearman"] >= orig["spearman"] - tol_ranqueamento),
         "auc_nao_piora": bool(cal["auc_dia_alto"] >= orig["auc_dia_alto"] - tol_ranqueamento),
         "mae_nao_sobe_mais_de_1gwh": bool(cal["mae_gwh"] <= orig["mae_gwh"] + tol_mae_gwh)}
    r["aceito"] = all(r.values())
    return r


def baselines(t: pd.DataFrame, idx: pd.Index, defasagem_min: int = 1) -> pd.DataFrame:
    y = t[ALVO]
    return pd.DataFrame({"persistencia": y.shift(defasagem_min),
                         "media7": y.shift(defasagem_min).rolling(7).mean()}).loc[idx]


def metricas(real: pd.Series, pred: pd.Series, ref_mae: float | None = None,
             limiar: pd.Series | None = None) -> dict:
    ok = real.notna() & pred.notna()
    real, pred = real[ok], pred[ok]
    erro = np.abs(real - pred)
    mae = float(np.mean(erro))
    grande = real >= PISO_MAPE_GWH
    out = {"mae_gwh": mae, "rmse_gwh": float(np.sqrt(np.mean((real - pred) ** 2))),
           "vies_pct": float(100 * (pred.sum() / real.sum() - 1)),
           "wape_pct": float(100 * erro.sum() / real.sum()),
           "mape_pct": float(100 * np.mean(erro[grande] / real[grande])) if grande.any() else float("nan"),
           "mape_n_dias": int(grande.sum()),
           "correlacao": float(np.corrcoef(real, pred)[0, 1]),
           "spearman": float(real.corr(pred, method="spearman"))}
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

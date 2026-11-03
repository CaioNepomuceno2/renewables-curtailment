"""Números de apoio à seção "Na prática" do README (lidos de reports/, sem dados brutos).

Saída: reports/aplicacao.json
"""
import json

import pandas as pd

from curtailment import REPORTS

out = {}

# janela horária do corte (últimos 12 meses)
h = pd.read_csv(REPORTS / "perfil_horario_12m.csv")
tot = h.mwh_dia.sum()
janela = h[h.hora.between(7, 15)].mwh_dia.sum()
out["frac_corte_7h_15h"] = round(float(janela / tot), 4)

# concentração por conjunto/usina (últimos 12 meses)
c = pd.read_csv(REPORTS / "top_conjuntos_12m.csv")
r12 = json.loads((REPORTS / "resumo.json").read_text())
total = r12["corte_gwh_12m"]
out["n_conjuntos_listados"] = len(c)
top15 = c.nlargest(15, "corte_gwh")
out["frac_corte_top15_conjuntos"] = round(float(top15.corte_gwh.sum() / total), 4)
out["n_top15_perda_acima_25pct"] = int((top15.pct_perdida > 25).sum())

# previsor: o que acontece nos dias que o modelo aponta
p = pd.read_csv(REPORTS / "previsao_diaria.csv", parse_dates=["dia"])
k = round(0.2 * len(p))
top = p.nlargest(k, "pred_completo")
out["previsor"] = {
    "n_dias_teste": len(p),
    "corte_medio_real_gwh_dia": round(float(p.real_gwh.mean()), 1),
    "corte_medio_real_top20pct_gwh_dia": round(float(top.real_gwh.mean()), 1),
    "frac_corte_total_nos_top20pct_dias": round(float(top.real_gwh.sum() / p.real_gwh.sum()), 4),
    "mae_modelo_gwh_dia": round(float((p.real_gwh - p.pred_completo).abs().mean()), 1),
    "mae_persistencia_gwh_dia": round(float((p.real_gwh - p.persistencia).abs().dropna().mean()), 1),
}
pv = out["previsor"]
pv["reducao_erro_gwh_dia"] = round(pv["mae_persistencia_gwh_dia"] - pv["mae_modelo_gwh_dia"], 1)

(REPORTS / "aplicacao.json").write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
print(json.dumps(out, indent=2, ensure_ascii=False))

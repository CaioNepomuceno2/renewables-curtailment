"""Diagnóstico: totais por ano, fonte, razão, UF, conjunto e perfil horário."""
import json

from curtailment import INTERIM, REPORTS
from curtailment import analise as A
from curtailment.build import RAZOES

semi = INTERIM / "coff_semihora.parquet"
REPORTS.mkdir(exist_ok=True)
U12 = ("2025-11-01", "2026-11-01")  # últimos 12 meses fechados
filtro12 = f"instante >= '{U12[0]}' and instante < '{U12[1]}'"

anual = A.resumo(semi, ["year(instante) as ano", "fonte"])
mensal = A.resumo(semi, ["strftime(instante, '%Y-%m') as mes", "fonte"])
razao = A.resumo(semi, ["year(instante) as ano", "fonte", "coalesce(razao, 'sem limitação') as razao"])
uf12 = A.resumo(semi, ["uf", "fonte"], filtro12).sort_values("corte_gwh", ascending=False)
sub12 = A.resumo(semi, ["subsistema", "fonte"], filtro12)
total12 = A.resumo(semi, ["fonte"], filtro12)
top = A.top_conjuntos(semi, *U12)
perfil = A.perfil_horario(semi, *U12)
diario = A.diario_total(semi)

for nome, df in {"resumo_anual": anual, "resumo_mensal": mensal, "por_razao": razao, "por_uf_12m": uf12,
                 "por_subsistema_12m": sub12, "top_conjuntos_12m": top, "perfil_horario_12m": perfil,
                 "resumo_12m": total12}.items():
    df.round(4).to_csv(REPORTS / f"{nome}.csv", index=False)
diario.to_parquet(INTERIM / "diario_total.parquet", index=False)

razao12 = A.resumo(semi, ["coalesce(razao, 'sem limitação') as razao"], filtro12 + " and razao is not null")
razao12["descricao"] = razao12.razao.map(RAZOES)
razao12.round(4).to_csv(REPORTS / "por_razao_12m.csv", index=False)

tot = total12[["corte_gwh", "potencial_gwh", "valor_cmo_rs_mi"]].sum()
resumo = {
    "periodo_12m": U12,
    "corte_gwh_12m": round(float(tot.corte_gwh), 1),
    "pct_perdida_12m": round(float(100 * tot.corte_gwh / tot.potencial_gwh), 2),
    "valor_cmo_rs_mi_12m": round(float(tot.valor_cmo_rs_mi), 1),
    "por_fonte_12m": total12.round(2).to_dict("records"),
    "por_razao_12m": razao12.round(2).to_dict("records"),
    "anual": anual.round(2).to_dict("records"),
}
(REPORTS / "resumo.json").write_text(json.dumps(resumo, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
print(json.dumps({k: v for k, v in resumo.items() if k != "anual"}, indent=1, ensure_ascii=False, default=str))
print(anual.round(2).to_string(index=False))
print(top.round(2).to_string(index=False))

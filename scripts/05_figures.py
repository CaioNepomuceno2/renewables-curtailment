"""Figuras do README."""
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

from curtailment import FIGURES, REPORTS

FIGURES.mkdir(parents=True, exist_ok=True)
AZUL, LARANJA, VERDE, CINZA, ROXO = "#2a78d6", "#eb6834", "#1baf7b", "#8a8a8a", "#7b5cd6"
COR = {"eolica": AZUL, "solar": LARANJA}
NOME = {"eolica": "Eólica", "solar": "Solar"}
plt.rcParams.update({"figure.dpi": 150, "axes.spines.top": False, "axes.spines.right": False,
                     "axes.grid": True, "grid.color": "#e3e3e3", "axes.axisbelow": True,
                     "font.size": 10, "axes.titleweight": "bold", "axes.titlesize": 11})


def br(x, d=0):
    return f"{x:,.{d}f}".replace(",", "X").replace(".", ",").replace("X", ".")


# 1. evolução mensal (GWh) e % perdida
m = pd.read_csv(REPORTS / "resumo_mensal.csv")
m["mes"] = pd.to_datetime(m.mes)
pv = m.pivot(index="mes", columns="fonte", values="corte_gwh").fillna(0)
tot = m.groupby("mes")[["corte_gwh", "potencial_gwh"]].sum()
fig, ax = plt.subplots(figsize=(10, 4.2))
base = None
for f in ["eolica", "solar"]:
    ax.bar(pv.index, pv[f] / 1e3, width=25, bottom=None if base is None else base / 1e3, color=COR[f],
           label=NOME[f])
    base = pv[f] if base is None else base + pv[f]
ax.set_ylabel("TWh cortados no mês")
ax2 = ax.twinx()
ax2.plot(tot.index, 100 * tot.corte_gwh / tot.potencial_gwh, color="#222", lw=1.6, label="% da geração possível")
ax2.set_ylabel("% da geração possível perdida")
ax2.spines["top"].set_visible(False)
ax2.grid(False)
ax2.set_ylim(0, None)
h1, l1 = ax.get_legend_handles_labels()
h2, l2 = ax2.get_legend_handles_labels()
ax.legend(h1 + h2, l1 + l2, loc="upper left", frameon=False)
ax.set_title("Energia eólica e solar cortada por restrição do ONS (constrained-off)\n"
             "a série solar começa em abr/2024", loc="left")
fig.tight_layout()
fig.savefig(FIGURES / "01_evolucao_mensal.png")
plt.close(fig)

# 2. por razão (últimos 12 meses) e por UF
r = pd.read_csv(REPORTS / "por_razao_12m.csv").query("corte_gwh > 1").sort_values("corte_gwh")
u = pd.read_csv(REPORTS / "por_uf_12m.csv")
u = u.pivot_table(index="uf", columns="fonte", values="corte_gwh", aggfunc="sum").fillna(0)
u = u.loc[u.sum(axis=1).sort_values().index[-10:]]
fig, (a1, a2) = plt.subplots(1, 2, figsize=(11, 4.2), gridspec_kw={"width_ratios": [1, 1.1]})
rot = {"ENE": "Energética\n(sobra de energia)", "CNF": "Confiabilidade\nelétrica",
       "REL": "Indisponibilidade\nexterna (rede)", "PAR": "Parecer de acesso"}
a1.barh([rot.get(x, x) for x in r.razao], r.corte_gwh / 1e3, color=[VERDE, ROXO, AZUL, CINZA][:len(r)])
for y, v in enumerate(r.corte_gwh / 1e3):
    a1.text(v, y, f"  {br(v, 1)} TWh", va="center")
a1.set_xlabel("TWh cortados")
a1.set_title("Por razão da restrição", loc="left")
a1.set_xlim(0, (r.corte_gwh.max() / 1e3) * 1.35)
left = None
for f in ["eolica", "solar"]:
    a2.barh(u.index, u[f] / 1e3, left=left, color=COR[f], label=NOME[f])
    left = u[f] / 1e3 if left is None else left + u[f] / 1e3
a2.set_xlabel("TWh cortados")
a2.set_title("Por estado (10 maiores)", loc="left")
a2.legend(frameon=False, loc="lower right")
fig.suptitle("Onde e por quê: últimos 12 meses (nov/2025 a out/2026)", x=0.01, ha="left", fontweight="bold")
fig.tight_layout()
fig.savefig(FIGURES / "02_razao_uf.png")
plt.close(fig)

# 3. perfil horário
p = pd.read_csv(REPORTS / "perfil_horario_12m.csv")
fig, ax = plt.subplots(figsize=(8, 3.8))
for f in ["eolica", "solar"]:
    s = p[p.fonte == f]
    ax.plot(s.hora, s.mwh_dia, color=COR[f], lw=2.2, marker="o", ms=3, label=NOME[f])
ax.set_xticks(range(0, 24, 3))
ax.set_xlabel("Hora do dia (como registrada no arquivo do ONS)")
ax.set_ylabel("MWh cortados por hora (média diária)")
ax.set_title("Quando o corte acontece: entre 7h e 15h, quando a solar inunda o sistema,\n"
             "e a eólica é cortada junto. Últimos 12 meses", loc="left")
ax.legend(frameon=False)
fig.tight_layout()
fig.savefig(FIGURES / "03_perfil_horario.png")
plt.close(fig)

# 4. top conjuntos
t = pd.read_csv(REPORTS / "top_conjuntos_12m.csv").head(12).iloc[::-1]
fig, ax = plt.subplots(figsize=(9, 4.8))
ax.barh(t.nom_usina.str.replace("Conj. ", "") + " (" + t.uf + ")", t.corte_gwh, color=[COR[f] for f in t.fonte])
for y, (v, pct) in enumerate(zip(t.corte_gwh, t.pct_perdida)):
    ax.text(v, y, f"  {br(v)} GWh ({br(pct)}%)", va="center", fontsize=9)
ax.set_xlim(0, t.corte_gwh.max() * 1.4)
ax.set_xlabel("GWh cortados (entre parênteses, % da geração possível)")
ax.set_title("Conjuntos com mais energia cortada: últimos 12 meses\nazul = eólica, laranja = solar", loc="left")
fig.tight_layout()
fig.savefig(FIGURES / "04_top_conjuntos.png")
plt.close(fig)

# 5. previsão do dia seguinte
d = pd.read_csv(REPORTS / "previsao_diaria.csv", parse_dates=["dia"])
d = d[d.dia >= "2026-05-01"]  # janela de 6 meses para legibilidade; o erro à direita é do período todo
met = pd.read_csv(REPORTS / "previsao_metricas.csv")
fig, (a1, a2) = plt.subplots(1, 2, figsize=(12, 4.2), gridspec_kw={"width_ratios": [2.2, 1]})
a1.plot(d.dia, d.real_gwh, color="#222", lw=1.2, label="Real")
a1.plot(d.dia, d.pred_completo, color=AZUL, lw=1.4, label="Modelo (informação de D-1)")
a1.plot(d.dia, d.persistencia, color=LARANJA, lw=0.9, alpha=0.7, label="Persistência (ontem)")
a1.set_ylabel("GWh cortados no dia")
a1.set_title("Previsão do corte do dia seguinte, fora da amostra (mai a out/2026)", loc="left")
a1.legend(frameon=False, ncol=3, loc="upper left")
sel = {"modelo_completo": "Modelo", "baseline_persistencia": "Persistência",
       "baseline_media7": "Média 7 dias", "baseline_corte_programado_pdp": "Corte programado\nno PDP"}
mm = met[(met.defasagem_min.isin([0, 1])) & met.metodo.isin(sel)].drop_duplicates("metodo")
mm = mm.set_index("metodo").loc[[k for k in sel if k in set(mm.metodo)]]
a2.barh([sel[k] for k in mm.index][::-1], mm.mae_gwh[::-1], color=[CINZA, CINZA, CINZA, AZUL][-len(mm):])
for y, v in enumerate(mm.mae_gwh[::-1]):
    a2.text(v, y, f"  {br(v, 1)}", va="center")
a2.set_xlim(0, mm.mae_gwh.max() * 1.3)
a2.set_xlabel("Erro absoluto médio (GWh/dia), menor é melhor")
a2.set_title("Erro jul/2025 a out/2026", loc="left")
fig.tight_layout()
fig.savefig(FIGURES / "05_previsao.png")
plt.close(fig)
print(sorted(p.name for p in FIGURES.glob("*.png")))

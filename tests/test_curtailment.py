import numpy as np
import pandas as pd
import pytest

from curtailment import analise, build, ons, previsao


def test_energia_cortada_so_conta_patamares_limitados():
    ger = [80.0, 80.0, 120.0, 50.0]
    ref = [100.0, 100.0, 100.0, np.nan]
    lim = [np.nan, 80.0, 80.0, 0.0]
    e = build.energia_cortada_mwh(ger, ref, lim)
    # sem limitação: zero mesmo com referência acima da geração
    assert e[0] == 0
    # limitado: (100 - 80) MWmed x 0,5 h
    assert e[1] == pytest.approx(10.0)
    # geração acima da referência não vira corte negativo
    assert e[2] == 0
    # referência ausente não gera corte
    assert e[3] == 0


def _semi_fake(tmp_path):
    raw = tmp_path / "raw"
    (raw / "eolica").mkdir(parents=True)
    (raw / "cmo").mkdir()
    t = pd.to_datetime(["2025-01-01 10:00", "2025-01-01 10:30"])
    pd.DataFrame({"id_subsistema": ["NE", "NE"], "id_estado": ["RN", "RN"], "nom_usina": ["X", "X"],
                  "id_ons": ["CJU_X", "CJU_X"], "ceg": ["-", "-"], "din_instante": t,
                  "val_geracao": [50.0, 100.0], "val_geracaolimitada": [50.0, None],
                  "val_disponibilidade": [100.0, 100.0], "val_geracaoreferencia": [90.0, 110.0],
                  "cod_razaorestricao": ["ENE", None], "cod_origemrestricao": ["SIS", None],
                  "id_pontoconexao": ["P", "P"], "nom_pontoconexao": ["P", "P"]}
                 ).to_parquet(raw / "eolica" / "2025-01.parquet")
    # CMO em texto, como em alguns anos do ONS
    pd.DataFrame({"id_subsistema": ["NE", "NE"], "din_instante": t, "val_cmo": ["100.0", "200,0"]}
                 ).to_parquet(raw / "cmo" / "2025.parquet")
    return build.semihoraria(raw, tmp_path / "semi.parquet")


def test_valoracao_pelo_cmo(tmp_path):
    s = pd.read_parquet(_semi_fake(tmp_path)).sort_values("instante")
    assert s.corte_mwh.tolist() == [20.0, 0.0]
    assert s.razao.tolist()[0] == "ENE" and pd.isna(s.razao.tolist()[1])
    assert s.valor_cmo_rs.tolist() == [2000.0, 0.0]


def test_agregar_pxp_corte_programado():
    d = pd.DataFrame({"dat_programacao": ["20250101"] * 3, "num_patamar": [1, 1, 2],
                      "cod_usinapdp": ["A", "B", "A"], "nom_usinapdp": ["A", "B", "A"],
                      "val_previsao": ["100.00", "50.00", "80.00"], "val_programado": ["60.00", "70.00", "80.00"]})
    a = ons.agregar_pxp(d).set_index("num_patamar")
    # o corte programado não compensa usina programada acima da previsão
    assert a.loc[1, "corte_programado"] == 40.0
    assert a.loc[2, "corte_programado"] == 0.0


def _tabela(n=60, seed=0):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2025-01-01", periods=n, freq="D")
    t = pd.DataFrame({"corte_gwh": rng.gamma(2, 20, n), "geracao_gwh": rng.gamma(20, 20, n),
                      "corte_gwh_eolica": rng.gamma(2, 10, n), "carga_sin_mw": rng.normal(80000, 3000, n),
                      "pxp_corte_programado_gwh": rng.gamma(2, 5, n)}, index=idx)
    return t


@pytest.mark.parametrize("k", [1, 2])
def test_features_nao_usam_o_proprio_dia(k):
    t = _tabela()
    f0 = previsao.features(t, k)
    t2 = t.copy()
    dia = t.index[40]
    # mudar o realizado do dia D (e de D-1 na variante estrita) não pode mudar as features de D
    t2.loc[t.index[40 - k + 1]:dia, ["corte_gwh", "geracao_gwh", "corte_gwh_eolica"]] *= 10
    f1 = previsao.features(t2, k)
    pd.testing.assert_series_equal(f0.loc[dia], f1.loc[dia])


def test_features_usam_ex_ante_do_proprio_dia():
    t = _tabela()
    t2 = t.copy()
    dia = t.index[40]
    t2.loc[dia, "pxp_corte_programado_gwh"] += 100
    assert (previsao.features(t2).loc[dia, "pxp_corte_programado_gwh"]
            - previsao.features(t).loc[dia, "pxp_corte_programado_gwh"]) == pytest.approx(100)


def test_metricas_skill_e_auc():
    real = pd.Series([0.0, 10.0, 20.0, 30.0])
    m = previsao.metricas(real, real.copy(), ref_mae=5.0, limiar=pd.Series([15.0] * 4))
    assert m["mae_gwh"] == 0 and m["skill_vs_persistencia"] == 1
    assert m["auc_dia_alto"] == 1.0


def test_cobertura_cmo(tmp_path):
    p = tmp_path / "semi.parquet"
    d = pd.DataFrame({"corte_mwh": [10.0, 30.0, 10.0], "cmo_rs_mwh": [100.0, 200.0, None]})
    d["valor_cmo_rs"] = d.corte_mwh * d.cmo_rs_mwh
    d.to_parquet(p)
    r = analise.cobertura_cmo(p)
    # 10 dos 50 MWh não têm CMO; a média ponderada usa só os 40 MWh que têm: (10 x 100 + 30 x 200) / 40
    assert r["pct_corte_sem_cmo"] == pytest.approx(20.0)
    assert r["cmo_medio_ponderado_rs_mwh"] == pytest.approx(175.0)

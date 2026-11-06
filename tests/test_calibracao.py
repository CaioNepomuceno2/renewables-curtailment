"""Testes da calibração de nível do previsor: só dados anteriores ao ponto previsto e núcleo ponta a ponta."""
import numpy as np
import pandas as pd
import pytest

from curtailment import previsao as P


def _oos(n=240, seed=0, vies=0.8):
    """Previsões fora da amostra sintéticas: o modelo prevê ``vies`` x o real, com ruído."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2025-01-01", periods=n, freq="D")
    real = pd.Series(rng.gamma(4, 25, n), index=idx)
    pred = vies * real * rng.normal(1, 0.1, n)
    return pd.DataFrame({"real": real, "pred": pred})


def test_fator_e_razao_das_somas_na_janela():
    idx = pd.date_range("2025-01-01", periods=10, freq="D")
    oos = pd.DataFrame({"real": [10.0] * 10, "pred": [5.0] * 5 + [10.0] * 5}, index=idx)
    # janela de 4 dias terminando em 10/01: só previsões iguais ao real
    assert P.fator_calibracao(oos, "2025-01-10", janela=4, min_dias=1) == pytest.approx(1.0)
    # janela de 10 dias: soma(real) / soma(pred) = 100 / 75, e não a média das razões
    assert P.fator_calibracao(oos, "2025-01-10", janela=10, min_dias=1) == pytest.approx(100 / 75)


def test_fator_ignora_dias_depois_do_ultimo_dia():
    idx = pd.date_range("2025-01-01", periods=10, freq="D")
    oos = pd.DataFrame({"real": [10.0] * 10, "pred": [5.0] * 10}, index=idx)
    oos.loc["2025-01-06":, ["real", "pred"]] = 1e6
    assert P.fator_calibracao(oos, "2025-01-05", janela=30, min_dias=1) == pytest.approx(2.0)


def test_fator_neutro_sem_dados_e_com_limites():
    oos = _oos(n=30)
    # menos dias que o mínimo: não calibra
    assert P.fator_calibracao(oos, "2025-01-30", janela=90, min_dias=60) == 1.0
    # previsão nula ou ausente: não calibra
    zero = oos.assign(pred=0.0)
    assert P.fator_calibracao(zero, "2025-01-30", janela=90, min_dias=1) == 1.0
    # previsão muito abaixo do real: a trava limita o fator
    assert P.fator_calibracao(oos.assign(pred=oos.real / 100), "2025-01-30", janela=90, min_dias=1) == 2.0
    assert P.fator_calibracao(oos.assign(pred=oos.real * 100), "2025-01-30", janela=90, min_dias=1) == 0.5


@pytest.mark.parametrize("k", [1, 2])
def test_calibrar_nivel_usa_so_dados_anteriores_ao_mes(k):
    oos = _oos()
    base = P.calibrar_nivel(oos, "2025-05-01", "2025-08-31", defasagem_min=k, janela=60, min_dias=30)
    for m in pd.period_range("2025-05", "2025-08", freq="M"):
        ini = m.start_time
        # adulterar real e previsão de D-k+1 em diante (inclusive o próprio mês) não muda o fator do mês
        futuro = oos.copy()
        futuro.loc[ini - pd.Timedelta(days=k - 1):, ["real", "pred"]] *= 7
        f1 = P.calibrar_nivel(futuro, "2025-05-01", "2025-08-31", defasagem_min=k, janela=60, min_dias=30)
        no_mes = (base.index >= ini) & (base.index <= m.end_time)
        np.testing.assert_allclose(f1.fator[no_mes], base.fator[no_mes])
        # mas um dia dentro da janela (até D-k) muda o fator: o teste não passa por o fator ser constante
        dentro = oos.copy()
        dentro.loc[ini - pd.Timedelta(days=k + 3), "real"] *= 7
        f2 = P.calibrar_nivel(dentro, "2025-05-01", "2025-08-31", defasagem_min=k, janela=60, min_dias=30)
        assert not np.allclose(f2.fator[no_mes], base.fator[no_mes])


def test_calibrar_nivel_corrige_o_nivel_e_mantem_a_ordem_do_mes():
    oos = _oos(vies=0.8)
    cal = P.calibrar_nivel(oos, "2025-06-01", "2025-08-31", janela=60, min_dias=30)
    real = oos.real.loc[cal.index]
    # o modelo prevê 80% do real: o fator sai perto de 1,25 e o viés cai de -20% para perto de zero
    assert cal.fator.between(1.1, 1.4).all()
    assert abs(cal.pred_cal.sum() / real.sum() - 1) < 0.05 < abs(cal.pred.sum() / real.sum() - 1)
    # o fator é constante no mês, então a ordem dos dias dentro do mês não muda
    for _, g in cal.groupby(cal.index.to_period("M")):
        assert g.fator.nunique() == 1
        assert g.pred.corr(g.pred_cal, method="spearman") == pytest.approx(1.0)


def _tabela_com_tendencia(n=230, seed=3):
    """Série diária com corte crescente: as árvores não extrapolam e o modelo subestima o total."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2025-01-01", periods=n, freq="D")
    nivel = np.linspace(20, 220, n)
    sem = 1 + 0.25 * np.sin(2 * np.pi * np.arange(n) / 7)
    corte = nivel * sem * rng.lognormal(0, 0.1, n)
    return pd.DataFrame({
        "corte_gwh": corte,
        "geracao_gwh": rng.gamma(30, 8, n),
        "corte_gwh_eolica": 0.6 * corte,
        "carga_sin_mw": rng.normal(80000, 2000, n),
        "pxp_corte_programado_gwh": 0.3 * nivel * sem * rng.lognormal(0, 0.1, n),
    }, index=idx)


def test_fumaca_features_previsao_calibracao_metrica():
    t = _tabela_com_tendencia()
    cols = list(P.features(t, 1).columns)
    pequeno = {**P.PARAMS, "n_estimators": 40, "num_leaves": 7, "min_child_samples": 5}
    # aquecimento (mar e abr) + teste (mai a jul); cada mês é previsto só com dias anteriores a ele
    oos = P.backtest(t, cols, "2025-03-01", "2025-07-31", "2025-01-01", 1, seed=0, params=pequeno)
    assert len(oos) == 31 + 30 + 31 + 30 + 31 and oos.index.is_monotonic_increasing
    cal = P.calibrar_nivel(oos, "2025-05-01", "2025-07-31", 1, janela=60, min_dias=30)
    teste = oos.loc["2025-05-01":]
    mb = P.metricas(teste.real, teste.pred, limiar=teste.limiar_alto)
    mc = P.metricas(teste.real, cal.pred_cal, limiar=teste.limiar_alto)
    for m in (mb, mc):
        assert all(np.isfinite(v) for v in m.values())
    # com tendência de alta, o modelo bruto subestima e a calibração reduz o viés sem desfazer a ordenação
    assert mb["vies_pct"] < -5
    assert abs(mc["vies_pct"]) < abs(mb["vies_pct"])
    assert mc["spearman"] > 0.5 and abs(mc["spearman"] - mb["spearman"]) < 0.1
    # a persistência do baseline segue a mesma interface
    b = P.baselines(t, teste.index, 1)
    assert np.isfinite(P.metricas(teste.real, b.persistencia)["mae_gwh"])


def test_metricas_novas_mape_wape_spearman():
    real = pd.Series([5.0, 20.0, 40.0, 100.0])
    pred = pd.Series([4.0, 10.0, 40.0, 110.0])
    m = P.metricas(real, pred)
    # o MAPE ignora o dia de 5 GWh (abaixo do piso de 10 GWh), o WAPE usa todos os dias
    assert m["mape_n_dias"] == 3
    assert m["mape_pct"] == pytest.approx(100 * np.mean([0.5, 0.0, 0.1]))
    assert m["wape_pct"] == pytest.approx(100 * 21 / 165)
    assert m["spearman"] == pytest.approx(1.0)


def test_regra_de_aceite():
    orig = {"vies_pct": -13.0, "spearman": 0.870, "auc_dia_alto": 0.913, "mae_gwh": 30.6}
    bom = {"vies_pct": 2.7, "spearman": 0.868, "auc_dia_alto": 0.911, "mae_gwh": 29.1}
    assert P.regra_de_aceite(orig, bom)["aceito"]
    # ranqueamento cai mais que a tolerância (0,005): rejeita, mesmo com viés e MAE melhores
    rank = P.regra_de_aceite(orig, {**bom, "spearman": 0.863})
    assert not rank["aceito"] and not rank["spearman_nao_piora"] and rank["vies_cai"] and rank["mae_nao_sobe_mais_de_1gwh"]
    assert not P.regra_de_aceite(orig, {**bom, "auc_dia_alto": 0.904})["auc_nao_piora"]
    # viés que só troca de sinal sem diminuir em módulo não conta como queda
    assert not P.regra_de_aceite(orig, {**bom, "vies_pct": 13.5})["vies_cai"]
    # MAE sobe mais de 1 GWh/dia
    assert not P.regra_de_aceite(orig, {**bom, "mae_gwh": 31.7})["mae_nao_sobe_mais_de_1gwh"]

"""Diagnóstico do constrained-off: quanto, onde, por quê e quando."""
from __future__ import annotations

from pathlib import Path

import duckdb
import pandas as pd


def _q(sql: str) -> pd.DataFrame:
    return duckdb.sql(sql).df()


def resumo(semi: Path, por: list[str], filtro: str = "true") -> pd.DataFrame:
    """GWh cortados, geração potencial (verificada + cortada), % perdida e valor pelo CMO."""
    g = ", ".join(por)
    df = _q(f"""
        select {g}, sum(corte_mwh) / 1e3 as corte_gwh,
               (sum(geracao_mwh) + sum(corte_mwh)) / 1e3 as potencial_gwh,
               sum(valor_cmo_rs) / 1e6 as valor_cmo_rs_mi,
               count(distinct id_ons) as n_usinas
        from read_parquet('{semi}') where {filtro} group by all order by all
    """)
    df["pct_perdida"] = 100 * df.corte_gwh / df.potencial_gwh
    return df


def top_conjuntos(semi: Path, inicio: str, fim: str, n: int = 15) -> pd.DataFrame:
    df = _q(f"""
        with b as (select * from read_parquet('{semi}')
                   where instante >= '{inicio}' and instante < '{fim}'),
        r as (select id_ons, razao, sum(corte_mwh) e from b where razao is not null group by all),
        dom as (select id_ons, arg_max(razao, e) as razao_dominante from r group by id_ons)
        select b.id_ons, any_value(nom_usina) nom_usina, any_value(fonte) fonte, any_value(uf) uf,
               any_value(subsistema) subsistema, any_value(ponto_conexao) ponto_conexao,
               sum(corte_mwh) / 1e3 corte_gwh, (sum(geracao_mwh) + sum(corte_mwh)) / 1e3 potencial_gwh,
               sum(valor_cmo_rs) / 1e6 valor_cmo_rs_mi, any_value(dom.razao_dominante) razao_dominante
        from b left join dom using (id_ons) group by b.id_ons order by corte_gwh desc limit {n}
    """)
    df["pct_perdida"] = 100 * df.corte_gwh / df.potencial_gwh
    return df


def perfil_horario(semi: Path, inicio: str, fim: str) -> pd.DataFrame:
    """Corte médio (MW) por hora do dia e fonte."""
    return _q(f"""
        select fonte, hour(instante) as hora, sum(corte_mwh) / count(distinct cast(instante as date)) as mwh_dia
        from read_parquet('{semi}') where instante >= '{inicio}' and instante < '{fim}'
        group by all order by fonte, hora
    """)


def diario_total(semi: Path) -> pd.DataFrame:
    """Série diária do SIN por fonte: corte, geração e minutos com limitação."""
    return _q(f"""
        select cast(instante as date) as dia, fonte, sum(corte_mwh) / 1e3 as corte_gwh,
               sum(geracao_mwh) / 1e3 as geracao_gwh, sum(valor_cmo_rs) / 1e6 as valor_cmo_rs_mi
        from read_parquet('{semi}') group by all order by dia, fonte
    """)

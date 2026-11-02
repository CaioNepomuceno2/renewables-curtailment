"""Energia cortada por constrained-off e sua valoração pelo CMO.

Definição (dicionário de dados do ONS, campo ``val_geracaonaorealizadaapurada``): em cada patamar
semi-horário em que o ONS limitou a usina (``val_geracaolimitada`` não nulo), a geração não realizada é
``max(geração de referência - geração verificada, 0)``. Nos patamares sem limitação ela é zero, mesmo que a
referência fique acima da geração verificada. Os valores vêm em MWmed por meia hora; a energia em MWh é o
valor x 0,5.

O mesmo cálculo é refeito aqui porque os arquivos antigos não trazem o campo apurado; nos meses em que ele
existe, os dois coincidem.
"""
from __future__ import annotations

from pathlib import Path

import duckdb
import numpy as np

RAZOES = {"REL": "Indisponibilidade externa (elétrica)", "CNF": "Confiabilidade elétrica",
          "ENE": "Razão energética (sobra de energia)", "PAR": "Parecer de acesso", "NI": "Não informada"}


def energia_cortada_mwh(geracao, referencia, limitada) -> np.ndarray:
    """Energia não realizada (MWh) por patamar semi-horário, a partir de valores em MWmed."""
    geracao = np.asarray(geracao, dtype=float)
    referencia = np.asarray(referencia, dtype=float)
    limitada = np.asarray(limitada, dtype=float)
    corte = np.clip(np.nan_to_num(referencia) - np.nan_to_num(geracao), 0, None)
    return np.where(np.isnan(limitada), 0.0, corte) * 0.5


def _sql_coff(raw: Path) -> str:
    partes = []
    for fonte in ["eolica", "solar"]:
        if list((raw / fonte).glob("*.parquet")):
            partes.append(f"select '{fonte}' as fonte, * from read_parquet('{raw / fonte}/*.parquet', union_by_name=true)")
    return " union all ".join(partes)


def semihoraria(raw: Path, out: Path) -> Path:
    """Base semi-horária por usina com energia cortada (MWh), razão e valor pelo CMO (R$)."""
    con = duckdb.connect()
    con.sql("set threads to 1")
    # val_cmo vem como texto em alguns anos
    cmo = f"""(select id_subsistema, din_instante,
                      try_cast(replace(cast(val_cmo as varchar), ',', '.') as double) as val_cmo
               from read_parquet('{raw}/cmo/*.parquet', union_by_name=true))"""
    con.sql(f"""
        copy (
          with c as ({_sql_coff(raw)}),
          base as (
            select fonte, id_subsistema as subsistema, id_estado as uf, id_ons, nom_usina, ceg,
                   nom_pontoconexao as ponto_conexao, din_instante as instante,
                   val_geracao * 0.5 as geracao_mwh,
                   val_geracaolimitada is not null as limitada,
                   case when val_geracaolimitada is null then 0
                        else greatest(coalesce(val_geracaoreferencia, 0) - coalesce(val_geracao, 0), 0) * 0.5
                   end as corte_mwh,
                   case when val_geracaolimitada is null then null
                        else coalesce(cod_razaorestricao, 'NI') end as razao,
                   cod_origemrestricao as origem
            from c
          )
          select b.*, m.val_cmo as cmo_rs_mwh, b.corte_mwh * m.val_cmo as valor_cmo_rs
          from base b left join {cmo} m
            on m.id_subsistema = b.subsistema and m.din_instante = b.instante
        ) to '{out}' (format parquet)
    """)
    return out


def diario_usina(semi: Path, out: Path) -> Path:
    """Agregado diário por usina/conjunto."""
    duckdb.sql(f"""
        copy (
          select fonte, subsistema, uf, id_ons, any_value(nom_usina) as nom_usina,
                 cast(instante as date) as dia,
                 sum(geracao_mwh) as geracao_mwh, sum(corte_mwh) as corte_mwh,
                 sum(valor_cmo_rs) as valor_cmo_rs, sum(limitada::int) * 30 as minutos_limitada
          from read_parquet('{semi}') group by all
        ) to '{out}' (format parquet)
    """)
    return out

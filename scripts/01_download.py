"""Baixa constrained-off (eólica e solar), CMO, previsão x programado e carga programada do ONS."""
import logging
from datetime import date

from curtailment import RAW, ons

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
ATE = "2026-10"  # último mês fechado
for fonte in ["eolica", "solar"]:
    ons.baixar_coff(fonte, RAW, ate=ATE)
ons.baixar_cmo(RAW, list(range(2021, 2027)))
ons.baixar_programacao_x_previsao(RAW, "2024-10-01", "2026-10-31")
ons.baixar_carga_programada(RAW, date(2021, 10, 1), date(2026, 10, 31))

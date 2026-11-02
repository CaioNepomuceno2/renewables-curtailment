"""Base semi-horária com energia cortada e valor pelo CMO, e agregado diário por usina."""
import duckdb

from curtailment import INTERIM, RAW, build

INTERIM.mkdir(parents=True, exist_ok=True)
semi = build.semihoraria(RAW, INTERIM / "coff_semihora.parquet")
build.diario_usina(semi, INTERIM / "coff_diario_usina.parquet")

# conferência: nos meses em que o ONS publica a GNRa apurada, ela deve bater com o cálculo
print(duckdb.sql(f"""
    select fonte, count(*) as linhas, sum(corte_mwh) / 1e3 as corte_gwh,
           count(*) filter (where cmo_rs_mwh is null and corte_mwh > 0) as cortes_sem_cmo
    from read_parquet('{semi}') group by fonte
"""))

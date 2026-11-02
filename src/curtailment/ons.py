"""Download dos dados abertos do ONS (CKAN + S3) e da API de carga programada."""
from __future__ import annotations

import logging
import re
import time
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import requests

log = logging.getLogger(__name__)

CKAN = "https://dados.ons.org.br/api/3/action/package_show"
API_CARGA = "https://apicarga.ons.org.br/prd/cargaprogramada"

DATASETS = {
    "eolica": "restricao_coff_eolica_usi",
    "solar": "restricao_coff_fotovoltaica",
}

# colunas mantidas da apuração de constrained-off (o resto é descritivo e pesa no disco)
COLS_COFF = [
    "id_subsistema", "id_estado", "nom_usina", "id_ons", "ceg", "din_instante", "val_geracao",
    "val_geracaolimitada", "val_disponibilidade", "val_geracaoreferencia", "cod_razaorestricao",
    "cod_origemrestricao", "id_pontoconexao", "nom_pontoconexao",
]


def _get(url: str, retries: int = 4, **kw) -> requests.Response:
    for k in range(retries):
        try:
            r = requests.get(url, timeout=300, **kw)
            r.raise_for_status()
            return r
        except requests.RequestException as e:
            if k == retries - 1:
                raise
            log.warning("falha em %s (%s); nova tentativa", url, e)
            time.sleep(2 ** (k + 1))
    raise RuntimeError("inalcançável")


def recursos(dataset: str, formato: str = "PARQUET") -> list[str]:
    res = _get(CKAN, params={"id": dataset}).json()["result"]["resources"]
    return sorted(r["url"] for r in res if r["format"].upper() == formato)


def recursos_mensais(dataset: str) -> dict[str, str]:
    """{"AAAA-MM": url} dos arquivos mensais em parquet."""
    out = {}
    for url in recursos(dataset):
        m = re.search(r"_(\d{4})_(\d{2})\.parquet$", url)
        if m:
            out[f"{m[1]}-{m[2]}"] = url
    return dict(sorted(out.items()))


def baixar_parquet(url: str, dest: Path, colunas: list[str] | None = None) -> Path:
    """Baixa um parquet, mantém só ``colunas`` e grava enxuto em ``dest``."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".tmp")
    tmp.write_bytes(_get(url).content)
    df = pd.read_parquet(tmp)
    if colunas is not None:
        df = df[[c for c in colunas if c in df.columns]]
    df.to_parquet(dest, index=False)
    tmp.unlink()
    return dest


def baixar_coff(fonte: str, dest_dir: Path, ate: str | None = None) -> list[Path]:
    """Arquivos mensais de constrained-off de uma fonte ("eolica" ou "solar")."""
    out = []
    for mes, url in recursos_mensais(DATASETS[fonte]).items():
        if ate and mes > ate:
            continue
        dest = dest_dir / fonte / f"{mes}.parquet"
        if not dest.exists():
            log.info("coff %s %s", fonte, mes)
            baixar_parquet(url, dest, COLS_COFF)
        out.append(dest)
    return out


def baixar_cmo(dest_dir: Path, anos: list[int]) -> list[Path]:
    urls = {int(re.search(r"_(\d{4})\.parquet$", u)[1]): u
            for u in recursos("cmo-semi-horario") if re.search(r"_(\d{4})\.parquet$", u)}
    out = []
    for ano in anos:
        dest = dest_dir / "cmo" / f"{ano}.parquet"
        if not dest.exists():
            log.info("cmo %s", ano)
            baixar_parquet(urls[ano], dest, ["id_subsistema", "din_instante", "val_cmo"])
        out.append(dest)
    return out


def baixar_programacao_x_previsao(dest_dir: Path, inicio: str, fim: str) -> Path:
    """Previsão x programado de eólicas e solares (arquivos diários), consolidado por dia e patamar."""
    dest = dest_dir / "programacao_x_previsao.parquet"
    urls = {}
    for url in recursos("programacao_x_previsao"):
        m = re.search(r"_(\d{4})_(\d{2})_(\d{2})\.parquet$", url)
        if m:
            urls[f"{m[1]}-{m[2]}-{m[3]}"] = url
    antigos = pd.read_parquet(dest) if dest.exists() else pd.DataFrame()
    feitos = set(antigos.dia.astype(str)) if len(antigos) else set()
    partes = [antigos] if len(antigos) else []
    for dia, url in sorted(urls.items()):
        if not (inicio <= dia <= fim) or dia in feitos:
            continue
        log.info("programacao_x_previsao %s", dia)
        tmp = dest_dir / "pxp.tmp"
        tmp.write_bytes(_get(url).content)
        d = pd.read_parquet(tmp)
        tmp.unlink()
        partes.append(agregar_pxp(d))
    out = pd.concat(partes, ignore_index=True).drop_duplicates(["dia", "num_patamar"])
    out.to_parquet(dest, index=False)
    return dest


def agregar_pxp(d: pd.DataFrame) -> pd.DataFrame:
    """Soma previsão e programado de todas as usinas eólicas e solares por dia e patamar (MWmed).

    O nome da usina no PDP não identifica a fonte de forma confiável, então o agregado é eólica + solar.
    """
    d = d.copy()
    for c in ["val_previsao", "val_programado"]:
        d[c] = pd.to_numeric(d[c].astype(str).str.strip().str.replace(",", "."), errors="coerce")
    d["dia"] = pd.to_datetime(d.dat_programacao.astype(str), format="%Y%m%d").dt.date.astype(str)
    d["corte_programado"] = (d.val_previsao - d.val_programado).clip(lower=0)
    return (d.groupby(["dia", "num_patamar"], as_index=False)
            [["val_previsao", "val_programado", "corte_programado"]].sum())


def baixar_carga_programada(dest_dir: Path, inicio: date, fim: date,
                            areas=("N", "NE", "S", "SECO")) -> Path:
    """Carga global programada (semi-horária) por área, consultando a API em blocos de 90 dias."""
    dest = dest_dir / "carga_programada.parquet"
    if dest.exists():
        return dest
    linhas = []
    for area in areas:
        ini = inicio
        while ini <= fim:
            f = min(ini + timedelta(days=89), fim)
            r = _get(API_CARGA, params={"dat_inicio": ini.isoformat(), "dat_fim": f.isoformat(),
                                        "cod_areacarga": area})
            linhas.extend(r.json())
            ini = f + timedelta(days=1)
    df = pd.DataFrame(linhas)
    df.to_parquet(dest, index=False)
    return dest

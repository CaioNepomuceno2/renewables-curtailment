"""Constrained-off de usinas eólicas e solares no SIN, com dados abertos do ONS."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
RAW = DATA / "raw"
INTERIM = DATA / "interim"
REPORTS = ROOT / "reports"
FIGURES = REPORTS / "figures"

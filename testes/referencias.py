"""Acesso aos casos exportados do Grasshopper em testes/referencias/<caso>/."""
import json
from pathlib import Path

import torch

PASTA = Path(__file__).resolve().parent / "referencias"


def casos_com(arquivo: str) -> list[Path]:
    """Pastas de caso que contêm `arquivo` (ex.: "sol_ladybug.json")."""
    if not PASTA.is_dir():
        return []
    return sorted(p for p in PASTA.iterdir() if (p / arquivo).is_file())


def carregar(caso: Path, arquivo: str) -> dict:
    with open(caso / arquivo, encoding="utf-8") as f:
        return json.load(f)


def carregar_malha(caso: Path, arquivo: str = "geometria.json"):
    d = carregar(caso, arquivo)
    return (
        torch.tensor(d["vertices"], dtype=torch.float64),
        torch.tensor(d["faces"], dtype=torch.int64),
    )

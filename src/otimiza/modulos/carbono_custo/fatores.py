"""Fatores de carbono e custo: tabela padrão (fatores.json) + trocas do usuário.

Trocas, aplicadas em ordem sobre a tabela padrão:
  1. `arquivo`: caminho de um JSON no mesmo formato (cópia editada do fatores.json);
  2. o resto do dicionário, no mesmo formato (ex.: {"anos": 30, "energia": {"tarifa": 0.95},
     "materiais": {"aco": {"kgco2e_kg": 1.2}}}).
Só valem chaves que já existem na tabela padrão (erro de digitação vira erro, não é ignorado);
listas (camadas das construções) são trocadas inteiras. O material de uma camada pode ser um
material novo, desde que declarado em "materiais".
"""
from __future__ import annotations

import copy
import json
from functools import lru_cache
from pathlib import Path

ARQUIVO_PADRAO = Path(__file__).with_name("fatores.json")
CONSTRUCOES = ("parede", "cobertura", "piso_solo", "piso_interno", "vidro")


@lru_cache(maxsize=1)
def _padrao() -> dict:
    return json.loads(ARQUIVO_PADRAO.read_text(encoding="utf-8"))


def padrao() -> dict:
    return copy.deepcopy(_padrao())


def _combinar(base: dict, extra: dict, caminho: str = "") -> None:
    for k, v in extra.items():
        if k.startswith("_") or k.startswith("fonte"):
            base[k] = v
            continue
        onde = f"{caminho}{k}"
        if k not in base:
            if caminho == "materiais.":       # material novo
                base[k] = {"kgco2e_kg": 0.0}
            else:
                raise ValueError(f"carbono_custo: chave desconhecida {onde!r} "
                                 f"(tem: {sorted(x for x in base if not x.startswith('_'))})")
        if isinstance(base[k], dict):
            if not isinstance(v, dict):
                raise ValueError(f"carbono_custo: {onde!r} deve ser um objeto")
            _combinar(base[k], v, onde + ".")
        else:
            base[k] = v


def _validar(f: dict) -> None:
    mats = f["materiais"]
    for nome, m in mats.items():
        if not isinstance(m, dict) or "kgco2e_kg" not in m:
            raise ValueError(f"carbono_custo: material {nome!r} precisa de 'kgco2e_kg'")
        float(m["kgco2e_kg"])
    for c in CONSTRUCOES:
        for camada in f["construcoes"][c]:
            if len(camada) != 3 or camada[0] not in mats:
                raise ValueError(f"carbono_custo: camada {camada!r} de {c!r} deve ser "
                                 "[material, espessura, densidade] com o material em 'materiais'")
    if f["estrutura"]["material"] not in mats:
        raise ValueError("carbono_custo: estrutura.material não está em 'materiais'")
    for k in ("anos", "taxa_desconto"):
        float(f[k])
    if float(f["anos"]) < 0:
        raise ValueError("carbono_custo: 'anos' deve ser ≥ 0")


def fatores_de(opcoes: dict | None) -> dict:
    """Tabela final: padrão ← arquivo ← resto das opções."""
    f = padrao()
    opcoes = dict(opcoes or {})
    arq = opcoes.pop("arquivo", None)
    if arq:
        p = Path(arq)
        if not p.is_file():
            raise ValueError(f"carbono_custo: arquivo de fatores não encontrado: {arq}")
        _combinar(f, json.loads(p.read_text(encoding="utf-8-sig")))
    _combinar(f, opcoes)
    _validar(f)
    return f


def por_m2(f: dict, construcao: str) -> tuple[float, float]:
    """(kg/m², kgCO2e/m²) de uma construção: Σ espessura × densidade (× fator)."""
    massa = carbono = 0.0
    for mat, esp, rho in f["construcoes"][construcao]:
        kg = float(esp) * float(rho)
        massa += kg
        carbono += kg * float(f["materiais"][mat]["kgco2e_kg"])
    return massa, carbono


def fator_anuidade(anos: float, taxa: float) -> float:
    """Σ_{t=1..N} 1/(1+i)^t: valor presente de 1 por ano durante N anos."""
    n = int(round(anos))
    if taxa == 0:
        return float(n)
    return (1 - (1 + taxa) ** -n) / taxa

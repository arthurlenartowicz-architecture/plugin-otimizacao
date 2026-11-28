"""Programa de necessidades e lote (o "programa.json").

Formato (tudo opcional; unidades em metros):
  {
    "lote": [[x, y], [x, y], ...],          # contorno do terreno (2D ou 3D: z = cota do terreno)
    "areas": {"sala": 30, "cozinha": 12},   # área-meta de cada uso (m²)
    "adjacencias": [["sala", "cozinha", 2], # pares que devem dividir parede (peso opcional, padrão 1)
                    "sala-varanda:1"],      # (também em texto: "a-b" ou "a-b:peso")
    "adjacencia": {"parede_max": 0.4, "comprimento_min": 0.8}
  }
Trocas, em ordem: `arquivo` (caminho de um JSON nesse formato) ← resto do dicionário. Cada
chave de cima substitui a do arquivo inteira (as `areas` também); `adjacencia` é combinada.
Chave desconhecida = erro. É o mesmo formato que os componentes Lote e Programa do GH escrevem.
"""
from __future__ import annotations

import json
from pathlib import Path

CHAVES = ("lote", "areas", "adjacencias", "adjacencia")
ADJACENCIA = {"parede_max": 0.4, "comprimento_min": 0.8}


def _erro(msg: str) -> ValueError:
    return ValueError("programa: " + msg)


def _lote(pts) -> tuple[list[list[float]], float | None]:
    if not isinstance(pts, list) or len(pts) < 3:
        raise _erro("'lote' deve ser uma lista com pelo menos 3 pontos [x, y] ou [x, y, z]")
    xy, zs = [], []
    for p in pts:
        if not isinstance(p, (list, tuple)) or len(p) not in (2, 3):
            raise _erro(f"ponto do lote inválido: {p!r}")
        xy.append([float(p[0]), float(p[1])])
        if len(p) == 3:
            zs.append(float(p[2]))
    if len(xy) > 3 and abs(xy[0][0] - xy[-1][0]) < 1e-9 and abs(xy[0][1] - xy[-1][1]) < 1e-9:
        xy.pop()                               # curva fechada: último ponto = primeiro
    if len(xy) < 3 or area_poligono(xy) < 1e-9:
        raise _erro("o lote não tem área (pontos alinhados ou repetidos)")
    return xy, (sum(zs) / len(zs) if zs else None)


def _adjacencia(item) -> tuple[str, str, float]:
    if isinstance(item, str):
        par, _, peso = item.partition(":")
        partes = [s.strip() for s in par.split("-")]
        if len(partes) != 2:
            raise _erro(f"adjacência em texto deve ser 'a-b' ou 'a-b:peso', não {item!r}")
        a, b = partes
        w = float(peso) if peso.strip() else 1.0
    elif isinstance(item, (list, tuple)) and len(item) in (2, 3):
        a, b = str(item[0]).strip(), str(item[1]).strip()
        w = float(item[2]) if len(item) == 3 else 1.0
    else:
        raise _erro(f"adjacência inválida: {item!r} (use ['a', 'b'] ou ['a', 'b', peso])")
    if not a or not b or a == b:
        raise _erro(f"adjacência precisa de dois usos diferentes: {item!r}")
    if w <= 0:
        raise _erro(f"peso da adjacência deve ser > 0: {item!r}")
    return a, b, w


def area_poligono(xy) -> float:
    """Área (m², sem sinal) pela fórmula do laço (shoelace)."""
    s = 0.0
    for (x0, y0), (x1, y1) in zip(xy, xy[1:] + xy[:1]):
        s += x0 * y1 - x1 * y0
    return abs(s) / 2


def programa_de(opcoes: dict | None) -> dict:
    """Programa normalizado: lote [[x, y]...] ou None, z_lote, area_lote, areas {uso: m²},
    adjacencias [(a, b, peso)], adjacencia {parede_max, comprimento_min}."""
    d = dict(opcoes or {})
    arq = d.pop("arquivo", None)
    bruto: dict = {}
    if arq:
        p = Path(arq)
        if not p.is_file():
            raise _erro(f"arquivo do programa não encontrado: {arq}")
        bruto.update(json.loads(p.read_text(encoding="utf-8-sig")))
        bruto.pop("arquivo", None)
    adj = {**ADJACENCIA, **(bruto.get("adjacencia") or {})}
    bruto.update(d)
    adj.update(d.get("adjacencia") or {})
    for k in bruto:
        if k not in CHAVES and not k.startswith("_"):
            raise _erro(f"chave desconhecida {k!r} (use {list(CHAVES)} ou 'arquivo')")
    for k in adj:
        if k not in ADJACENCIA:
            raise _erro(f"chave desconhecida 'adjacencia.{k}' (use {list(ADJACENCIA)})")
    adj = {k: float(v) for k, v in adj.items()}
    if min(adj.values()) < 0:
        raise _erro("parede_max e comprimento_min devem ser ≥ 0")

    lote, z = _lote(bruto["lote"]) if bruto.get("lote") else (None, None)
    areas = {}
    for uso, a in (bruto.get("areas") or {}).items():
        a = float(a)
        if a <= 0:
            raise _erro(f"área-meta de {uso!r} deve ser > 0")
        areas[str(uso).strip()] = a
    pares = [_adjacencia(x) for x in (bruto.get("adjacencias") or [])]
    return {"lote": lote, "z_lote": z, "area_lote": area_poligono(lote) if lote else None,
            "areas": areas, "adjacencias": pares, "adjacencia": adj}

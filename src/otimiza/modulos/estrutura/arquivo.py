"""Estrutura <-> dict JSON (casos de validação lidos pelo Grasshopper/Karamba).

Formato (metros, kN):
  nos      [[x, y, z], ...]  na ordem de 1ª aparição nas barras (como o Karamba numera)
  barras   [[i, j], ...]
  trelica  [bool, ...]
  secoes   [{"A", "Iy", "Iz", "J", "Wy", "Wz", "circular", "D"?, "t"?}, ...] por barra
  material {"E", "G", "rho", "fy", "gamma"}  (gamma = rho·g/1000 kN/m³, como no Karamba)
  apoios   [{"condicoes": [6 bools], "nos": [...]}, ...]  (grupos com a mesma condição)
  cargas   [{"no", "forca": [3], "momento": [3]}, ...]
  gravidade m/s² (0 = sem peso próprio)
"""
from __future__ import annotations

import torch

from otimiza.modulos.estrutura.fem import DT, PROPS, Estrutura


def renumerar(s: Estrutura) -> Estrutura:
    """Nós na ordem em que aparecem nas barras (i, j, i, j, ...); nós soltos no fim."""
    ordem = list(dict.fromkeys(s.barras.flatten().tolist()))
    vistos = set(ordem)
    ordem += [n for n in range(s.n_nos) if n not in vistos]
    novo = torch.empty(s.n_nos, dtype=torch.long)
    novo[torch.tensor(ordem)] = torch.arange(s.n_nos)
    return Estrutura(s.nos[ordem], novo[s.barras], s.props, s.fy, s.trelica, s.apoios[ordem],
                     s.cargas[ordem], s.carga_dist, s.gravidade)


def para_dict(s: Estrutura, nome: str, secao_tubo: tuple[float, float] | None = None) -> dict:
    s = renumerar(s)
    if bool(s.carga_dist.abs().sum() > 0):
        raise ValueError("casos do Karamba só com cargas nodais (a distribuída é validada analiticamente)")
    secoes = []
    for p in s.props:
        d = {k: float(v) for k, v in zip(PROPS[:7], p[:7])}
        d["circular"] = bool(d["circular"])
        if secao_tubo:
            d["D"], d["t"] = secao_tubo
        secoes.append(d)
    p0 = s.props[0]
    rho = float(p0[9])
    grupos: dict[tuple, list] = {}
    for n in range(s.n_nos):
        c = tuple(bool(x) for x in s.apoios[n])
        if any(c):
            grupos.setdefault(c, []).append(n)
    cargas = [{"no": n, "forca": s.cargas[n, :3].tolist(), "momento": s.cargas[n, 3:].tolist()}
              for n in range(s.n_nos) if bool(s.cargas[n].abs().sum() > 0)]
    return {
        "nome": nome, "unidades": "m, kN, kN/m², kg/m³",
        "nos": s.nos.tolist(), "barras": s.barras.tolist(), "trelica": s.trelica.tolist(),
        "secoes": secoes,
        "material": {"E": float(p0[7]), "G": float(p0[8]), "rho": rho, "fy": float(s.fy[0]),
                     "gamma": rho * 10.0 / 1000},
        "apoios": [{"condicoes": list(c), "nos": ns} for c, ns in grupos.items()],
        "cargas": cargas, "gravidade": s.gravidade,
    }


def de_dict(d: dict) -> Estrutura:
    nos = torch.tensor(d["nos"], dtype=DT)
    barras = torch.tensor(d["barras"], dtype=torch.long)
    m = d["material"]
    props = torch.tensor([[sc["A"], sc["Iy"], sc["Iz"], sc["J"], sc["Wy"], sc["Wz"],
                           float(sc["circular"]), m["E"], m["G"], m["rho"]] for sc in d["secoes"]],
                         dtype=DT)
    nn = nos.shape[0]
    ap = torch.zeros(nn, 6, dtype=torch.bool)
    for g in d["apoios"]:
        ap[g["nos"]] = torch.tensor(g["condicoes"], dtype=torch.bool)
    cg = torch.zeros(nn, 6, dtype=DT)
    for c in d["cargas"]:
        cg[c["no"], :3] += torch.tensor(c["forca"], dtype=DT)
        cg[c["no"], 3:] += torch.tensor(c["momento"], dtype=DT)
    return Estrutura(nos, barras, props, torch.full((barras.shape[0],), m["fy"], dtype=DT),
                     torch.tensor(d["trelica"], dtype=torch.bool), ap, cg,
                     torch.zeros(barras.shape[0], 3, dtype=DT), float(d.get("gravidade", 0.0)))

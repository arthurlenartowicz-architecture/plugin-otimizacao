"""Geometria da envoltória: área, volume, compacidade e WWR por orientação.

Envoltória = faces fachada + vidro + cobertura (normais para fora). Pisos não entram:
os pisos internos (rótulo piso) de um prédio de vários pavimentos estragariam a conta.
- Volume pelo teorema da divergência com o campo (0, 0, z − z_base):
  V = Σ (z_centro − z_base) · n_z · A. Faces verticais dão zero, e a base (no z_base) também,
  então a malha pode vir aberta embaixo (prédio apoiado no chão).
- A base que falta é fechada pela conta: área da base = Σ n_z · A (projeção horizontal);
  se a base já estiver na malha, essa soma dá zero. Área = Σ A + base.
- Paredes: ângulo entre a normal e o zênite entre 60° e 130° (como o Honeybee);
  orientação pelo azimute da normal: N (315–45°), L (45–135°), S (135–225°), O (225–315°).
"""
from __future__ import annotations

import math

import torch

from otimiza.nucleo.cena import CODIGO, Variante
from otimiza.nucleo.geometria import normais_e_areas

ORIENTACOES = ("N", "L", "S", "O")
NZ_PAREDE = (math.cos(math.radians(130)), math.cos(math.radians(60)))


def orientacao(normais: torch.Tensor) -> torch.Tensor:
    """(F,) 0..3 = N, L, S, O para paredes; −1 para o resto (coberturas, pisos)."""
    nz = normais[:, 2]
    az = torch.rad2deg(torch.atan2(normais[:, 0], normais[:, 1])) % 360
    o = (((az + 45) % 360) // 90).long()
    parede = (nz >= NZ_PAREDE[0]) & (nz <= NZ_PAREDE[1])
    return torch.where(parede, o, torch.full_like(o, -1))


def envoltoria(v: Variante) -> dict:
    """Área (m²), volume (m³), área de piso (m²) e áreas de parede/vidro por orientação (4,)."""
    vert = v.vertices.double()
    rot = v.rotulos
    env = (rot == CODIGO["fachada"]) | (rot == CODIGO["vidro"]) | (rot == CODIGO["cobertura"])
    f = v.faces[env]
    n, a = normais_e_areas(vert, f)
    zc = vert[f, 2].mean(dim=1)
    z_base = vert[f.flatten(), 2].min() if f.numel() else torch.tensor(0.0, dtype=torch.float64)
    volume = ((zc - z_base) * n[:, 2] * a).sum().abs()
    base = (n[:, 2] * a).sum().clamp(min=0)
    area = a.sum() + base

    parede, vidro = torch.zeros(4, dtype=torch.float64), torch.zeros(4, dtype=torch.float64)
    o = orientacao(n)
    eh_vidro = rot[env] == CODIGO["vidro"]
    ok = o >= 0
    parede.index_add_(0, o[ok], a[ok])
    vidro.index_add_(0, o[ok & eh_vidro], a[ok & eh_vidro])

    fp = v.faces[rot == CODIGO["piso"]]
    area_piso = normais_e_areas(vert, fp)[1].sum() if fp.numel() else torch.tensor(0.0, dtype=torch.float64)
    return {"area": area, "volume": volume, "area_piso": area_piso,
            "parede": parede, "vidro": vidro}

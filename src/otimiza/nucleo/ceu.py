"""Subdivisão do céu em patches: Tregenza (MF=1, 145) e Reinhart (MF=2, 577).

Mesma lógica do `reinhartb.cal` do Radiance:
- o céu é cortado em 7·MF faixas de altitude de largura alpha = 90 / (7·MF + 0,5)
  graus, mais uma calota no zênite (meia faixa);
- a faixa i tem MF · (30, 30, 24, 24, 18, 12, 6)[i // MF] patches;
- dentro de cada faixa, o 1º patch é centrado no norte e a numeração segue no
  sentido horário (para o leste). Numeração da faixa do horizonte até o zênite.

A ordem dos patches ainda precisa ser conferida contra o Ladybug/Honeybee
(script do Grasshopper, etapa 1a/1b) antes de comparar matrizes de céu.
O patch de chão não é incluído aqui; `indice()` devolve -1 para direções abaixo
do horizonte.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import torch

PATCHES_TREGENZA = (30, 30, 24, 24, 18, 12, 6)


@dataclass
class Ceu:
    mf: int
    alpha: float                   # largura da faixa, graus
    n_por_faixa: torch.Tensor      # (F,) patches em cada faixa (a última é a calota = 1)
    inicio_faixa: torch.Tensor     # (F,) índice do 1º patch de cada faixa
    faixa: torch.Tensor            # (P,) faixa de cada patch
    altitude: torch.Tensor         # (P,) altitude do centro, graus
    azimute: torch.Tensor          # (P,) azimute do centro, graus (norte, horário)
    angulo_solido: torch.Tensor    # (P,) sr; soma = 2π

    @property
    def n_patches(self) -> int:
        return int(self.altitude.shape[0])

    @property
    def vetores(self) -> torch.Tensor:
        """(P, 3) direção do centro de cada patch (x leste, y norte, z cima)."""
        alt = torch.deg2rad(self.altitude)
        az = torch.deg2rad(self.azimute)
        return torch.stack(
            [torch.cos(alt) * torch.sin(az), torch.cos(alt) * torch.cos(az), torch.sin(alt)], dim=-1
        )

    def indice(self, vetores: torch.Tensor) -> torch.Tensor:
        """Patch que contém cada direção (N, 3). -1 abaixo do horizonte."""
        v = vetores.to(self.altitude)
        v = v / v.norm(dim=-1, keepdim=True)
        alt = torch.rad2deg(torch.asin(v[..., 2].clamp(-1, 1)))
        az = torch.remainder(torch.rad2deg(torch.atan2(v[..., 0], v[..., 1])), 360.0)

        n_faixas = self.n_por_faixa.shape[0]
        f = torch.floor(alt / self.alpha).long().clamp(0, n_faixas - 1)
        n = self.n_por_faixa[f].to(az)
        j = torch.remainder(torch.floor((az + 180.0 / n) / (360.0 / n)), n).long()
        idx = self.inicio_faixa[f] + j
        return torch.where(v[..., 2] < 0, torch.full_like(idx, -1), idx)


def criar_ceu(mf: int = 1, device=None) -> Ceu:
    """mf=1: Tregenza (145 patches); mf=2: Reinhart (577); mf=4: 2305."""
    if mf < 1:
        raise ValueError("mf precisa ser >= 1")
    alpha = 90.0 / (7 * mf + 0.5)
    n_por_faixa = [mf * PATCHES_TREGENZA[i // mf] for i in range(7 * mf)] + [1]

    faixa, alt, az, omega, inicio = [], [], [], [], []
    for i, n in enumerate(n_por_faixa):
        inicio.append(len(alt))
        a0 = i * alpha
        a1 = min((i + 1) * alpha, 90.0)
        calota = i == len(n_por_faixa) - 1
        centro = 90.0 if calota else (a0 + a1) / 2
        # ângulo sólido de um anel: 2π (sen a1 − sen a0), dividido entre os n patches
        anel = 2 * math.pi * (math.sin(math.radians(a1)) - math.sin(math.radians(a0)))
        for j in range(n):
            faixa.append(i)
            alt.append(centro)
            az.append(0.0 if calota else j * 360.0 / n)
            omega.append(anel / n)

    f64 = dict(dtype=torch.float64, device=device)
    return Ceu(
        mf=mf,
        alpha=alpha,
        n_por_faixa=torch.tensor(n_por_faixa, dtype=torch.int64, device=device),
        inicio_faixa=torch.tensor(inicio, dtype=torch.int64, device=device),
        faixa=torch.tensor(faixa, dtype=torch.int64, device=device),
        altitude=torch.tensor(alt, **f64),
        azimute=torch.tensor(az, **f64),
        angulo_solido=torch.tensor(omega, **f64),
    )

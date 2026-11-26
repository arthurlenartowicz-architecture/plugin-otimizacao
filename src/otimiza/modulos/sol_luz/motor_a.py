"""Motor A do sol_luz: radiação incidente, horas de sol, sombra no entorno, FV.

Mesmo método do Ladybug (LB Incident Radiation / LB Direct Sun Hours):
- o céu é um conjunto de direções (centros dos patches) com energia acumulada
  E_p (kWh/m², já inclui o sol direto repartido nos patches);
- radiação no sensor s = Σ_p visível(s, p) · max(0, n_s · v_p) · E_p
- horas de sol no sensor s = Σ_t visível(s, sol_t) · [n_s · sol_t > 0] · Δt

O solo entra como patches espelhados (abaixo do horizonte) com radiância
uniforme L_solo = ρ · G_horiz / π (Lambertiano), onde G_horiz é a global
horizontal do próprio céu. Sem reflexões entre edifícios (isso é o motor B).
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import torch

from otimiza.nucleo.matriz_ceu import MatrizCeu
from otimiza.nucleo.raios import Cena

LOTE_SENSORES = 100_000  # limita a memória da matriz sensores × direções


@dataclass
class CeuDirecional:
    vetores: torch.Tensor  # (D, 3) direções (céu e, se houver, solo)
    energia: torch.Tensor  # (D,) kWh/m² acumulado no período, por direção
    n_ceu: int             # as primeiras n_ceu direções são do céu


def ceu_direcional(matriz: MatrizCeu, refletancia_solo: float = 0.2, horas=None) -> CeuDirecional:
    """Transforma a matriz de céu em direções + energia acumulada.

    horas: máscara ou índices das horas a somar (padrão: o ano todo).
    """
    L = matriz.total if horas is None else matriz.total[:, horas]
    ceu = matriz.ceu
    om = ceu.angulo_solido.to(L)
    v = ceu.vetores.to(L)
    energia_ceu = (L * om[:, None]).sum(dim=1) / 1000.0  # kWh/m²
    if refletancia_solo <= 0:
        return CeuDirecional(v, energia_ceu, ceu.n_patches)

    g_horiz = (energia_ceu * v[:, 2]).sum()  # global horizontal acumulada, kWh/m²
    v_solo = v * torch.tensor([1.0, 1.0, -1.0], dtype=v.dtype, device=v.device)
    energia_solo = refletancia_solo * g_horiz / math.pi * om
    return CeuDirecional(torch.cat([v, v_solo]), torch.cat([energia_ceu, energia_solo]), ceu.n_patches)


def radiacao_incidente(cena: Cena, pontos, normais, ceu: CeuDirecional, variante=None) -> torch.Tensor:
    """(S,) kWh/m² recebido por cada sensor no período."""
    dev = cena.device
    v = ceu.vetores.to(device=dev, dtype=torch.float32)
    e = ceu.energia.to(device=dev, dtype=torch.float32)
    saida = []
    for i in range(0, pontos.shape[0], LOTE_SENSORES):
        sl = slice(i, i + LOTE_SENSORES)
        var = variante[sl] if isinstance(variante, torch.Tensor) else variante
        n = normais[sl].to(device=dev, dtype=torch.float32)
        vis = cena.visibilidade(pontos[sl], v, variante=var)
        peso = (n @ v.T).clamp(min=0) * vis
        saida.append(peso @ e)
    return torch.cat(saida)


def horas_de_sol(cena: Cena, pontos, normais, vetores_sol, variante=None, horas_por_vetor=1.0):
    """(S,) horas de sol direto em cada sensor. vetores_sol (T, 3) apontam para o sol."""
    dev = cena.device
    vs = vetores_sol.to(device=dev, dtype=torch.float32)
    saida = []
    for i in range(0, pontos.shape[0], LOTE_SENSORES):
        sl = slice(i, i + LOTE_SENSORES)
        var = variante[sl] if isinstance(variante, torch.Tensor) else variante
        vis = cena.visibilidade(pontos[sl], vs, variante=var, normais=normais[sl])
        saida.append(vis.sum(dim=1).float() * horas_por_vetor)
    return torch.cat(saida)


def perda_de_sol(cena_sem: Cena, cena_com: Cena, pontos, normais, vetores_sol, **kw):
    """Sombra no entorno: horas de sol que os sensores perdem com o projeto."""
    return horas_de_sol(cena_sem, pontos, normais, vetores_sol, **kw) - \
        horas_de_sol(cena_com, pontos, normais, vetores_sol, **kw)


def fotovoltaico(radiacao_kwh_m2, areas_m2, eficiencia: float = 0.20, perdas: float = 0.14):
    """Geração FV simples (kWh): radiação × área × eficiência do módulo × (1 − perdas).

    Sem temperatura nem ângulo de incidência; serve para comparar variantes.
    """
    return radiacao_kwh_m2 * areas_m2.to(radiacao_kwh_m2) * eficiencia * (1 - perdas)

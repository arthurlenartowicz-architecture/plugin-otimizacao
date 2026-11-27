"""Conforto externo: temperatura radiante média (SolarCal) e UTCI, em lote.

Mesmo método do LB Outdoor Solar MRT + LB UTCI Comfort (ladybug_comfort):
- MRT = T_superfícies + ΔMRT_curta + ΔMRT_longa, com T_superfícies = T_ar (padrão do LB);
- ΔMRT_longa = 0,5 · exposição_ao_céu · (T_céu − T_ar), T_céu pela radiação infravermelha
  horizontal do EPW (σ, emissividade do corpo 0,95), como o EnergyPlus;
- ΔMRT_curta (SolarCal, Arens et al. 2015; sol abaixo de 2° = 0): fluxo no corpo =
  fp(alt) · fração_do_corpo_ao_sol · DNI + 0,5 · céu · fe · DHI + 0,5 · céu · fe · GH · ρ_piso,
  ERF = fluxo · α/ε, ΔMRT = ERF / (fe · 6,012); pessoa em pé (fe = 0,725), sol a 135° do
  rosto (fp tabelado), α = 0,7, ε = 0,95, ρ_piso = 0,25, GH = DHI + DNI · sen(alt).
- UTCI: polinômio de Bröde et al. 2009 (210 termos, coeficientes em dados_conforto.json,
  extraídos do ladybug_comfort por scripts/gerar_ref_termico.py), vento a 10 m limitado a
  0,5–17 m/s, pressão de vapor pela fórmula do próprio UTCI.

O ar, a umidade e o vento são os mesmos para todos os pontos (EPW); só a MRT muda com a
geometria. Por isso o polinômio vira, a cada hora, um polinômio de grau 6 em ΔT = MRT − T_ar
(7 coeficientes por hora, calculados uma vez por clima).
"""
from __future__ import annotations

import json
import math
from functools import lru_cache
from pathlib import Path

import torch

SIGMA = 5.6697e-8          # Stefan-Boltzmann (valor do EnergyPlus/Ladybug)
FE_EM_PE = 0.725          # fração do corpo que troca radiação (em pé)
HR = 6.012                # coeficiente de troca radiante do SolarCal, W/m²K
ABSORT, EMISS = 0.7, 0.95
RHO_PISO = 0.25
UTCI_CONFORTO = (9.0, 26.0)  # sem estresse térmico


@lru_cache(maxsize=1)
def dados() -> dict:
    with open(Path(__file__).with_name("dados_conforto.json"), encoding="utf-8") as f:
        return json.load(f)


def pressao_vapor_saturado_hpa(ta: torch.Tensor) -> torch.Tensor:
    """Fórmula de saturação usada pelo UTCI (hPa), ta em °C."""
    g = (-2836.5744, -6028.076559, 19.54263612, -0.02737830188, 0.000016261698,
         7.0229056e-10, -1.8680009e-13)
    tk = ta.double() + 273.15
    es = 2.7150305 * torch.log(tk)
    for i, x in enumerate(g):
        es = es + x * tk ** (i - 2)
    return torch.exp(es) * 0.01


def utci_coeficientes(ta, vel, rh) -> torch.Tensor:
    """(..., 7) coeficientes do UTCI como polinômio em ΔT = MRT − T_ar (grau 0..6)."""
    d = dados()
    ta = torch.as_tensor(ta, dtype=torch.float64)
    vel = torch.as_tensor(vel, dtype=torch.float64, device=ta.device).clamp(0.5, 17.0)
    rh = torch.as_tensor(rh, dtype=torch.float64, device=ta.device)
    pa = pressao_vapor_saturado_hpa(ta) * rh / 100.0 / 10.0  # kPa
    ta, vel, pa = torch.broadcast_tensors(ta, vel, pa)
    c = torch.zeros(*ta.shape, 7, dtype=torch.float64, device=ta.device)
    for (a, k, v, p), coef in zip(d["utci_expoentes"], d["utci_coef"]):
        c[..., k] += coef * ta ** a * vel ** v * pa ** p
    return c


def utci_de_delta(coef: torch.Tensor, delta: torch.Tensor) -> torch.Tensor:
    """UTCI com os coeficientes (H, 7) e ΔT (..., H), pelo método de Horner (no tipo dos
    coeficientes: float32 basta no módulo, erro < 1e-3 °C)."""
    delta = delta.to(coef.dtype)
    u = torch.zeros_like(delta) + coef[:, 6]
    for k in range(5, -1, -1):
        u = u * delta + coef[:, k]
    return u


def utci(ta, tr, vel, rh) -> torch.Tensor:
    """UTCI (°C) ponto a ponto (todos os argumentos com a mesma forma ou escalares)."""
    ta = torch.as_tensor(ta, dtype=torch.float64)
    tr = torch.as_tensor(tr, dtype=torch.float64, device=ta.device)
    ta, tr = torch.broadcast_tensors(ta, tr)
    c = utci_coeficientes(ta, vel, rh)
    u = c[..., 6]
    d = tr - ta
    for k in range(5, -1, -1):
        u = u * d + c[..., k]
    return u


def temperatura_ceu(ir_horiz: torch.Tensor, emissividade: float = EMISS) -> torch.Tensor:
    """Temperatura do céu (°C) pela radiação infravermelha horizontal (W/m²)."""
    return (ir_horiz.double() / (emissividade * SIGMA)).clamp(min=0) ** 0.25 - 273.15


def fator_projecao(altitude: torch.Tensor) -> torch.Tensor:
    """Fração da área do corpo projetada para o sol (SolarCal, em pé, sol a 135°)."""
    tab = torch.tensor(dados()["solarcal_proj_135"], dtype=torch.float64, device=altitude.device)
    i = (torch.ceil(altitude.double()).long() - 1).clamp(0, tab.numel() - 1)
    return tab[i]


def mrt_externa(ta, ir_horiz, dhi, dni, altitude, ceu_exposto, corpo_ao_sol,
                rho_piso: float = RHO_PISO) -> torch.Tensor:
    """(S, H) MRT (°C). Séries horárias (H,); ceu_exposto (S,) entre 0 e 1 (fração do céu
    vista, por ângulo sólido); corpo_ao_sol (S, H) fração do corpo ao sol direto."""
    dev, tipo = corpo_ao_sol.device, corpo_ao_sol.dtype
    ta, ir, dhi, dni, alt = (torch.as_tensor(x, dtype=torch.float64).to(dev)
                             for x in (ta, ir_horiz, dhi, dni, altitude))
    # séries horárias (H,) em float64; a combinação (S, H) no tipo de corpo_ao_sol
    dt_ceu = 0.5 * (temperatura_ceu(ir) - ta)                     # ΔMRT longa com céu todo
    k = (ABSORT / EMISS) / (FE_EM_PE * HR) * (alt >= 2)           # fluxo → ΔMRT, 0 sem sol
    gh = dhi + dni * torch.sin(torch.deg2rad(alt))
    direto = k * fator_projecao(alt) * dni                        # × fração do corpo ao sol
    difuso = k * 0.5 * FE_EM_PE * (dhi + gh * rho_piso) + dt_ceu  # × fração do céu vista
    ceu = torch.as_tensor(ceu_exposto, dtype=tipo).to(dev)[:, None]
    return ta.to(tipo) + corpo_ao_sol * direto.to(tipo) + ceu * difuso.to(tipo)


def faixas_utci(u: torch.Tensor) -> dict[str, torch.Tensor]:
    """% das horas (última dimensão) em conforto, calor (> 26) e frio (< 9)."""
    lo, hi = UTCI_CONFORTO
    return {"conforto": ((u >= lo) & (u <= hi)).double().mean(-1) * 100,
            "calor": (u > hi).double().mean(-1) * 100,
            "frio": (u < lo).double().mean(-1) * 100}


def exposicao(raios, pontos, variante, vetores_ceu, angulo_solido, vetores_sol):
    """Exposição de pessoas (pontos sem normal, como o LB Human to Sky Relation com 1 ponto):
    (S,) fração do céu vista (patches ponderados pelo ângulo sólido, soma = 2π) e
    (S, T) 0/1 se o ponto vê o sol em cada vetor de vetores_sol."""
    om = (angulo_solido.double() / (2 * math.pi)).to(raios.device)
    vis = raios.visibilidade(pontos, vetores_ceu, variante=variante)
    ceu = vis.double() @ om
    sol = raios.visibilidade(pontos, vetores_sol, variante=variante)
    return ceu, sol

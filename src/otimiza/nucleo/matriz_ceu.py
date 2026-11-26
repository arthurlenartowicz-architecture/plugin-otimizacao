"""Matriz de céu anual pelo modelo de Perez (equivalente ao `gendaymtx -O1`).

Resultado: radiância (W/sr/m²) de cada patch do céu em cada hora, separada em
difusa (céu) e direta (sol). Só radiação solar por enquanto; a versão visível
(lux, eficácia luminosa de Perez 1990) entra na etapa 1c.

Passos para cada hora com sol acima do horizonte e DHI > 0:
1. ε (limpidez) e Δ (brilho) a partir de DNI, DHI, zênite solar e massa de ar
2. coeficientes a..e de Perez et al. (1993) pela faixa de ε
3. luminância relativa de cada patch:
   lv = (1 + a·exp(b / cos ζ)) · (1 + c·exp(d·γ) + e·cos²γ)
   ζ = zênite do patch, γ = ângulo entre patch e sol
4. escala para que Σ L·Ω·cos ζ = DHI (conserva a difusa horizontal)
5. sol: DNI repartida entre os 4 patches mais próximos com peso
   1 / (1,002 − cos γ), como no gendaymtx; Σ L_sol·Ω = DNI
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import torch

from otimiza.nucleo.ceu import Ceu
from otimiza.nucleo.clima import Clima
from otimiza.nucleo.sol import PosicaoSolar, posicao_solar_clima

# limites superiores das 7 primeiras faixas de ε (a 8ª vai até infinito)
LIMITES_EPS = (1.065, 1.230, 1.500, 1.950, 2.800, 4.500, 6.200)

# Perez et al. (1993), tabela de coeficientes de luminância:
# [faixa][a, b, c, d, e][x1, x2, x3, x4]
COEF_PEREZ = (
    ((1.3525, -0.2576, -0.2690, -1.4366), (-0.7670, 0.0007, 1.2734, -0.1233),
     (2.8000, 0.6004, 1.2375, 1.0000), (1.8734, 0.6297, 0.9738, 0.2809),
     (0.0356, -0.1246, -0.5718, 0.9938)),
    ((-1.2219, -0.7730, 1.4148, 1.1016), (-0.2054, 0.0367, -3.9128, 0.9156),
     (6.9750, 0.1774, 6.4477, -0.1239), (-1.5798, -0.5081, -1.7812, 0.1080),
     (0.2624, 0.0672, -0.2190, -0.4285)),
    ((-1.1000, -0.2515, 0.8952, 0.0156), (0.2782, -0.1812, -4.5000, 1.1766),
     (24.7219, -13.0812, -37.7000, 34.8438), (-5.0000, 1.5218, 3.9229, -2.6204),
     (-0.0156, 0.1597, 0.4199, -0.5562)),
    ((-0.5484, -0.6654, -0.2672, 0.7117), (0.7234, -0.6219, -5.6812, 2.6297),
     (33.3389, -18.3000, -62.2500, 52.0781), (-3.5000, 0.0016, 1.1477, 0.1062),
     (0.4659, -0.3296, -0.0876, -0.0329)),
    ((-0.6000, -0.3566, -2.5000, 2.3250), (0.2937, 0.0496, -5.6812, 1.8415),
     (21.0000, -4.7656, -21.5906, 7.2492), (-3.5000, -0.1554, 1.4062, 0.3988),
     (0.0032, 0.0766, -0.0656, -0.1294)),
    ((-1.0156, -0.3670, 1.0078, 1.4051), (0.2875, -0.5328, -3.8500, 3.3750),
     (14.0000, -0.9999, -7.1406, 7.5469), (-3.4000, -0.1078, -1.0750, 1.5702),
     (-0.0672, 0.4016, 0.3017, -0.4844)),
    ((-1.0000, 0.0211, 0.5025, -0.5119), (-0.3000, 0.1922, 0.7023, -1.6317),
     (19.0000, -5.0000, 1.2438, -1.9094), (-4.0000, 0.0250, 0.3844, 0.2656),
     (1.0468, -0.3788, -2.4517, 1.4656)),
    ((-1.0500, 0.0289, 0.4260, 0.3590), (-0.3250, 0.1156, 0.7781, 0.0025),
     (31.0625, -14.5000, -46.1148, 55.3750), (-7.2312, 0.4050, 13.3500, 0.6234),
     (1.5000, -0.6426, 1.8564, 0.5636)),
)

CONSTANTE_SOLAR = 1367.0  # W/m²
DIAS_ANTES_DO_MES = (0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334)


@dataclass
class MatrizCeu:
    difusa: torch.Tensor  # (P, H) radiância W/sr/m² (ou luminância cd/m² se luz=True)
    direta: torch.Tensor  # (P, H)
    ceu: Ceu
    unidade: str = "W/sr/m2"

    @property
    def total(self) -> torch.Tensor:
        return self.difusa + self.direta

    def acumulado_kwh(self, qual: str = "total") -> torch.Tensor:
        """(P,) irradiância acumulada de cada patch no ano, kWh/m² (Σ L·Ω / 1000)."""
        L = getattr(self, qual)
        return (L * self.ceu.angulo_solido.to(L)[:, None]).sum(dim=1) / 1000.0


def parametros_perez(zenite_rad, dni, dhi, dia_do_ano):
    """ε (limpidez) e Δ (brilho do céu) de Perez."""
    k = 1.041 * zenite_rad**3
    eps = ((dhi + dni) / dhi.clamp(min=1e-9) + k) / (1 + k)
    zen_graus = torch.rad2deg(zenite_rad).clamp(max=93.8)
    massa_ar = 1.0 / (torch.cos(zenite_rad) + 0.15 * (93.885 - zen_graus) ** -1.253)
    e0 = CONSTANTE_SOLAR * (1 + 0.033 * torch.cos(2 * math.pi * dia_do_ano / 365.0))
    delta = dhi * massa_ar / e0
    # faixa de validade do ajuste de Perez (mesmos limites do gendaylit)
    return eps.clamp(min=1.0), delta.clamp(0.01, 0.6)


def coeficientes_perez(eps, delta, zenite_rad) -> torch.Tensor:
    """(H, 5) coeficientes a, b, c, d, e para cada hora."""
    tabela = torch.tensor(COEF_PEREZ, dtype=eps.dtype, device=eps.device)  # (8, 5, 4)
    faixa = torch.bucketize(eps, torch.tensor(LIMITES_EPS, dtype=eps.dtype, device=eps.device))
    x = tabela[faixa]  # (H, 5, 4)
    Z = zenite_rad[:, None]
    D = delta[:, None]
    coef = x[..., 0] + x[..., 1] * Z + D * (x[..., 2] + x[..., 3] * Z)

    # faixa 1 (céu encoberto) tem fórmulas próprias para c e d
    p = faixa == 0
    if p.any():
        xc, xd = x[p, 2], x[p, 3]
        Zp, Dp = zenite_rad[p], delta[p]
        coef[p, 2] = torch.exp((Dp * (xc[:, 0] + xc[:, 1] * Zp)) ** xc[:, 2]) - xc[:, 3]
        coef[p, 3] = -torch.exp(Dp * (xd[:, 0] + xd[:, 1] * Zp)) + xd[:, 2] + Dp * xd[:, 3]
    return coef


def luminancia_relativa(coef, cos_zeta, cos_gama) -> torch.Tensor:
    """(H, P) distribuição relativa de Perez. cos_zeta (P,), cos_gama (H, P)."""
    a, b, c, d, e = (coef[:, i : i + 1] for i in range(5))
    gama = torch.acos(cos_gama.clamp(-1, 1))
    gradacao = 1 + a * torch.exp(b / cos_zeta.clamp(min=0.01)[None, :])
    indicatriz = 1 + c * torch.exp(d * gama) + e * cos_gama**2
    return gradacao * indicatriz


def matriz_ceu(
    clima: Clima, ceu: Ceu, sol: PosicaoSolar | None = None, sol_patches: int = 4, device=None,
    luz: bool = False,
) -> MatrizCeu:
    """luz=False: radiância solar (W/sr/m²), como gendaymtx -O1.
    luz=True: luminância (cd/m²) pela eficácia de Perez; Σ L·Ω·cos dá lux."""
    sol = sol or posicao_solar_clima(clima)
    f64 = dict(dtype=torch.float64, device=device)
    dni = clima["dni"].to(**f64)
    dhi = clima["dhi"].to(**f64)
    alt = sol.altitude.to(**f64)
    v_sol = sol.vetores.to(**f64)                       # (H, 3)
    v_patch = ceu.vetores.to(**f64)                     # (P, 3)
    omega = ceu.angulo_solido.to(**f64)                 # (P,)
    cos_zeta = v_patch[:, 2]
    dia_do_ano = torch.tensor(DIAS_ANTES_DO_MES, **f64)[clima.mes.to(device) - 1] + clima.dia.to(**f64)

    H, P = dni.shape[0], v_patch.shape[0]
    difusa = torch.zeros(P, H, **f64)
    direta = torch.zeros(P, H, **f64)
    unidade = "cd/m2" if luz else "W/sr/m2"

    dia = (alt > 0) & ((dhi > 0) | (dni > 0))
    if not dia.any():
        return MatrizCeu(difusa, direta, ceu, unidade)
    idx = dia.nonzero(as_tuple=True)[0]
    cos_gama = v_sol[idx] @ v_patch.T                   # (h, P)

    # --- difusa
    com_difusa = dhi[idx] > 0
    if com_difusa.any():
        i = idx[com_difusa]
        zen = torch.deg2rad(90.0 - alt[i])
        eps, delta = parametros_perez(zen, dni[i], dhi[i], dia_do_ano[i])
        lv = luminancia_relativa(coeficientes_perez(eps, delta, zen), cos_zeta, cos_gama[com_difusa])
        lv = lv.clamp(min=0)
        horiz = (lv * omega * cos_zeta).sum(dim=1, keepdim=True)  # difusa horizontal relativa
        difusa[:, i] = (lv * dhi[i, None] / horiz).T

    # --- direta: DNI nos patches mais próximos do sol
    com_sol = dni[idx] > 0
    if com_sol.any():
        i = idx[com_sol]
        cg = cos_gama[com_sol]
        k = min(sol_patches, P)
        cos_k, p_k = cg.topk(k, dim=1)
        peso = 1.0 / (1.002 - cos_k)
        peso = peso / peso.sum(dim=1, keepdim=True)
        rad = dni[i, None] * peso / omega[p_k]          # radiância W/sr/m²
        bloco = torch.zeros(i.shape[0], P, **f64)
        bloco.scatter_add_(1, p_k, rad)
        direta[:, i] = bloco.T

    if luz:  # W/m² -> lux pela eficácia de Perez (ε e Δ continuam radiométricos)
        k_dif, k_dir = eficacia_luminosa(clima, sol, device=device)
        difusa = difusa * k_dif[None, :]
        direta = direta * k_dir[None, :]
    return MatrizCeu(difusa, direta, ceu, unidade)


# Perez et al. (1990), eficácia luminosa (lm/W) por faixa de ε: [a, b, c, d]
EFICACIA_DIFUSA = (
    (97.24, -0.46, 12.00, -8.91), (107.22, 1.15, 0.59, -3.95), (104.97, 2.96, -5.53, -8.77),
    (102.39, 5.59, -13.95, -13.90), (100.71, 5.94, -22.75, -23.74), (106.42, 3.83, -36.15, -28.83),
    (141.88, 1.90, -53.24, -14.03), (152.23, 0.35, -45.27, -7.98),
)
EFICACIA_DIRETA = (
    (57.20, -4.55, -2.98, 117.12), (98.99, -3.46, -1.21, 12.38), (109.83, -4.90, -1.71, -8.81),
    (110.34, -5.84, -1.99, -4.56), (106.36, -3.97, -1.75, -6.16), (107.19, -1.25, -1.51, -26.73),
    (105.75, 0.77, -1.26, -34.44), (101.18, 1.58, -1.10, -8.29),
)


def eficacia_luminosa(clima: Clima, sol: PosicaoSolar, device=None):
    """(H,) eficácias luminosas difusa e direta (lm/W), Perez et al. 1990.

    K_dif = a + b·W + c·cos Z + d·ln Δ
    K_dir = max(0, a + b·W + c·exp(5,73·Z − 5) + d·Δ)
    W = água precipitável (cm) estimada do ponto de orvalho: exp(0,07·Td − 0,075)
    """
    f64 = dict(dtype=torch.float64, device=device)
    dni = clima["dni"].to(**f64)
    dhi = clima["dhi"].to(**f64)
    zen = torch.deg2rad(90.0 - sol.altitude.to(**f64)).clamp(max=math.radians(89.9))
    dia_do_ano = torch.tensor(DIAS_ANTES_DO_MES, **f64)[clima.mes.to(device) - 1] + clima.dia.to(**f64)
    eps, delta = parametros_perez(zen, dni, dhi.clamp(min=1e-6), dia_do_ano)
    faixa = torch.bucketize(eps, torch.tensor(LIMITES_EPS, **f64))
    w = torch.exp(0.07 * clima["temp_orvalho"].to(**f64) - 0.075)
    a, b, c, d = torch.tensor(EFICACIA_DIFUSA, **f64)[faixa].unbind(-1)
    k_dif = a + b * w + c * torch.cos(zen) + d * torch.log(delta)
    a, b, c, d = torch.tensor(EFICACIA_DIRETA, **f64)[faixa].unbind(-1)
    k_dir = (a + b * w + c * torch.exp(5.73 * zen - 5) + d * delta).clamp(min=0)
    return k_dif.clamp(min=0), k_dir

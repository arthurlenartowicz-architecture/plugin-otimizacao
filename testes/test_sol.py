"""Validação da posição solar.

Referência principal: caso de exemplo do SPA do NREL (Reda & Andreas, 2004,
"Solar Position Algorithm for Solar Radiation Applications", tabela A4.1):
Golden, Colorado, 17/10/2003 12:30:30 (fuso -7), pressão 820 mbar, 11 °C
-> zênite 50,11162°, azimute 194,34024°.
"""
import torch

from otimiza.nucleo.clima import ler_epw
from otimiza.nucleo.sol import posicao_solar, posicao_solar_clima

TOL_GRAUS = 0.02  # precisão nominal do algoritmo da NOAA: ~0,0167°


def test_caso_spa_nrel():
    p = posicao_solar(
        39.742476, -105.1786, -7.0, 10, 17, 12 + 30 / 60 + 30 / 3600, ano=2003,
        pressao_mbar=820.0, temperatura_c=11.0,
    )
    assert abs(p.zenite.item() - 50.11162) < TOL_GRAUS
    assert abs(p.azimute.item() - 194.34024) < TOL_GRAUS


def test_declinacao_solsticio_e_equinocio():
    # solstício de junho de 2017: 21/06 04:24 UTC -> declinação = obliquidade (~23,437°)
    p = posicao_solar(0.0, 0.0, 0.0, 6, 21, 4.4, ano=2017)
    assert abs(p.declinacao.item() - 23.437) < 0.01
    # equinócio de março de 2017: 20/03 10:29 UTC -> declinação ~0
    p = posicao_solar(0.0, 0.0, 0.0, 3, 20, 10 + 29 / 60, ano=2017)
    assert abs(p.declinacao.item()) < 0.01


def test_equacao_do_tempo_extremos():
    # um valor por dia do ano: extremos conhecidos ~ +16,4 min (nov) e -14,2 min (fev)
    dias = torch.arange(365, dtype=torch.float64)
    p = posicao_solar(0.0, 0.0, 0.0, 1, 1 + dias, 12.0, ano=2017)  # dia 32 = 1º de fev etc.
    assert 16.3 < p.equacao_tempo.max().item() < 16.6
    assert -14.4 < p.equacao_tempo.min().item() < -14.1


def test_sao_paulo_meio_dia_inverno():
    # hemisfério sul, junho: sol ao norte, altitude ~ 90 - 23,55 - 23,44 = 43,0°
    # meio-dia solar em SP ~ 12h07 local (longitude -46,63 vs. meridiano -45)
    p = posicao_solar(-23.55, -46.63, -3.0, 6, 21, 12 + 8 / 60)
    assert abs(p.altitude.item() - 43.0) < 0.3
    az = p.azimute.item()
    assert min(az, 360 - az) < 3  # perto do norte


def test_manha_leste_tarde_oeste():
    p = posicao_solar(-23.55, -46.63, -3.0, 3, 21, torch.tensor([8.0, 16.0]))
    assert 45 < p.azimute[0] < 135   # leste
    assert 225 < p.azimute[1] < 315  # oeste


def test_vetores_unitarios():
    p = posicao_solar(-23.55, -46.63, -3.0, 12, 21, torch.linspace(5, 19, 50))
    v = p.vetores
    assert v.shape == (50, 3)
    assert torch.allclose(v.norm(dim=-1), torch.ones(50, dtype=torch.float64))
    assert torch.allclose(v[:, 2], torch.sin(torch.deg2rad(p.altitude)))


def test_ano_inteiro_do_epw(epw_sintetico):
    c = ler_epw(epw_sintetico)
    p = posicao_solar_clima(c)
    assert p.altitude.shape == (8760,)
    # em SP o sol fica acima do horizonte ~metade do tempo
    frac = p.acima_horizonte.double().mean().item()
    assert 0.48 < frac < 0.53

import math

import pytest
import torch

from otimiza.nucleo.ceu import criar_ceu


@pytest.mark.parametrize("mf, n", [(1, 145), (2, 577), (4, 2305)])
def test_numero_de_patches(mf, n):
    assert criar_ceu(mf).n_patches == n


def test_tregenza_faixas():
    c = criar_ceu(1)
    assert c.alpha == pytest.approx(12.0)
    assert c.n_por_faixa.tolist() == [30, 30, 24, 24, 18, 12, 6, 1]
    assert c.altitude[0] == pytest.approx(6.0)
    assert c.altitude[-1] == pytest.approx(90.0)


@pytest.mark.parametrize("mf", [1, 2, 4])
def test_angulo_solido_soma_hemisferio(mf):
    c = criar_ceu(mf)
    assert c.angulo_solido.sum().item() == pytest.approx(2 * math.pi, rel=1e-12)


@pytest.mark.parametrize("mf", [1, 2])
def test_centro_cai_no_proprio_patch(mf):
    c = criar_ceu(mf)
    assert torch.equal(c.indice(c.vetores), torch.arange(c.n_patches))


def test_ordem_norte_depois_leste():
    c = criar_ceu(1)
    assert c.azimute[0] == 0.0          # 1º patch centrado no norte
    assert c.azimute[1] == pytest.approx(12.0)  # o próximo fica a leste
    v = c.vetores
    assert v[0, 1] > 0.9 and abs(v[0, 0]) < 1e-12  # aponta para +y (norte)
    assert v[1, 0] > 0                  # x positivo = leste


def test_abaixo_do_horizonte():
    c = criar_ceu(1)
    assert c.indice(torch.tensor([[0.0, 1.0, -0.1]])).item() == -1


@pytest.mark.parametrize("mf", [1, 2])
def test_monte_carlo_angulo_solido(mf):
    # direções uniformes no hemisfério: fração em cada patch ≈ Ω / 2π
    g = torch.Generator().manual_seed(0)
    n = 2_000_000
    z = torch.rand(n, generator=g, dtype=torch.float64)  # uniforme em z = uniforme em área
    phi = 2 * math.pi * torch.rand(n, generator=g, dtype=torch.float64)
    r = torch.sqrt(1 - z**2)
    v = torch.stack([r * torch.cos(phi), r * torch.sin(phi), z], dim=-1)
    c = criar_ceu(mf)
    contagem = torch.bincount(c.indice(v), minlength=c.n_patches).double() / n
    esperado = c.angulo_solido / (2 * math.pi)
    desvio = torch.sqrt(esperado / n)  # ruído estatístico da amostragem
    assert ((contagem - esperado).abs() < 5 * desvio).all()

"""Motor A contra resultados analíticos (EPW real de data/epw)."""
import math

import pytest
import torch

from otimiza.modulos.sol_luz.motor_a import (
    ceu_direcional, fotovoltaico, horas_de_sol, perda_de_sol, radiacao_incidente,
)
from otimiza.nucleo.ceu import criar_ceu
from otimiza.nucleo.clima import ler_epw
from otimiza.nucleo.geometria import caixa, retangulo
from otimiza.nucleo.matriz_ceu import matriz_ceu
from otimiza.nucleo.raios import Cena
from otimiza.nucleo.sol import posicao_solar_clima
from testes.conftest import EPWS_REAIS

pytestmark = pytest.mark.skipif(not EPWS_REAIS, reason="nenhum EPW real em data/epw")

ORIGEM = torch.zeros(1, 3)
CIMA = torch.tensor([[0, 0, 1.0]])
BAIXO = torch.tensor([[0, 0, -1.0]])


@pytest.fixture(scope="module")
def dados():
    clima = ler_epw(EPWS_REAIS[0])
    sol = posicao_solar_clima(clima)
    m = matriz_ceu(clima, criar_ceu(1), sol)
    return clima, sol, m


def cena_vazia():
    return Cena([retangulo(1e4, 1e4, 1e4 + 1, 1e4 + 1)])  # nada por perto


def test_horizontal_livre_igual_global_horizontal(dados):
    clima, sol, m = dados
    ceu = ceu_direcional(m, refletancia_solo=0.0)
    r = radiacao_incidente(cena_vazia(), ORIGEM, CIMA, ceu).item()
    dia = sol.altitude > 0
    ghi = (clima["dhi"][dia] + clima["dni"][dia] * torch.sin(torch.deg2rad(sol.altitude[dia]))).sum() / 1000
    # o sol vai para os centros dos patches: pequeno erro no cosseno
    assert r == pytest.approx(ghi.item(), rel=0.02)


def test_solo_so_ve_quem_olha_para_baixo(dados):
    _, _, m = dados
    sem = ceu_direcional(m, refletancia_solo=0.0)
    com = ceu_direcional(m, refletancia_solo=0.2)
    cena = cena_vazia()
    assert radiacao_incidente(cena, ORIGEM, CIMA, com).item() == pytest.approx(
        radiacao_incidente(cena, ORIGEM, CIMA, sem).item(), rel=1e-6)
    # virado para baixo e livre: recebe ρ·G_horiz (soma discreta de Ω·cos ≈ π)
    g = radiacao_incidente(cena, ORIGEM, CIMA, sem).item()
    baixo = radiacao_incidente(cena, ORIGEM, BAIXO, com).item()
    assert baixo == pytest.approx(0.2 * g, rel=0.01)


def test_hemisferio_sul_fachada_norte_recebe_mais(dados):
    clima, _, m = dados
    assert clima.local.latitude < 0
    ceu = ceu_direcional(m)
    cena = cena_vazia()
    norte = radiacao_incidente(cena, ORIGEM, torch.tensor([[0, 1.0, 0]]), ceu).item()
    sul = radiacao_incidente(cena, ORIGEM, torch.tensor([[0, -1.0, 0]]), ceu).item()
    assert norte > 1.5 * sul


def test_cobertura_grande_bloqueia_o_ceu(dados):
    _, _, m = dados
    ceu = ceu_direcional(m, refletancia_solo=0.0)
    cena = Cena([retangulo(-500, -500, 500, 500, z=1.0)])
    assert radiacao_incidente(cena, ORIGEM, CIMA, ceu).item() < 1.0


def test_horas_de_sol_e_perda(dados):
    _, sol, _ = dados
    vs = sol.vetores[sol.altitude > 0]
    livre = horas_de_sol(cena_vazia(), ORIGEM, CIMA, vs).item()
    assert livre == vs.shape[0]
    # torre alta ao norte (hemisfério sul: o sol passa pelo norte)
    torre = Cena([caixa(-10, 5, 0, 10, 15, 60)])
    sombreado = horas_de_sol(torre, ORIGEM, CIMA, vs).item()
    assert 0 < sombreado < 0.7 * livre
    perda = perda_de_sol(cena_vazia(), torre, ORIGEM, CIMA, vs).item()
    assert perda == pytest.approx(livre - sombreado)


def test_fotovoltaico():
    kwh = fotovoltaico(torch.tensor([1500.0]), torch.tensor([10.0]), eficiencia=0.2, perdas=0.1)
    assert kwh.item() == pytest.approx(1500 * 10 * 0.2 * 0.9)


def test_lote_maior_que_o_limite(dados, monkeypatch):
    # divide em lotes sem mudar o resultado
    import otimiza.modulos.sol_luz.motor_a as ma
    _, _, m = dados
    ceu = ceu_direcional(m)
    pts = torch.zeros(10, 3)
    nrm = CIMA.repeat(10, 1)
    inteiro = radiacao_incidente(cena_vazia(), pts, nrm, ceu)
    monkeypatch.setattr(ma, "LOTE_SENSORES", 3)
    em_lotes = radiacao_incidente(cena_vazia(), pts, nrm, ceu)
    assert torch.allclose(inteiro, em_lotes)

"""1e: motor C (olho-de-peixe e DGP) contra resultados analíticos."""
import math
import time

import pytest
import torch

from otimiza.modulos.sol_luz.motor_b import Materiais
from otimiza.modulos.sol_luz.motor_c import (
    OMEGA_SOL, avaliar_imagem, coeficientes_vistas, dgp, grade_olho_de_peixe, imagem,
    indice_de_posicao, luminancia_pixels,
)
from otimiza.nucleo.ceu import criar_ceu
from otimiza.nucleo.geometria import caixa, retangulo
from otimiza.nucleo.raios import Cena

CEU = criar_ceu(1)


def mat(n, rho=0.0, tau=0.0):
    return Materiais.de_listas([(torch.full((n,), rho), torch.full((n,), tau))])


def ceu_uniforme(L=1000.0, solo=0.0):
    col = torch.full((CEU.n_patches + 1, 1), L, dtype=torch.float64)
    col[-1] = solo
    return col


def test_angulos_solidos_da_grade():
    g = grade_olho_de_peixe(128)
    assert g.omega.sum().item() == pytest.approx(2 * math.pi, rel=0.01)
    assert (g.omega * g.vista[:, 2]).sum().item() == pytest.approx(math.pi, rel=0.01)


def test_indice_de_posicao():
    def p(alt_graus, lado_graus):
        # direção a partir de ângulos acima/ao lado da linha de visão
        a, b = math.radians(alt_graus), math.radians(lado_graus)
        v = torch.tensor([[math.sin(b) * math.cos(a), math.sin(a), math.cos(b) * math.cos(a)]],
                         dtype=torch.float64)
        return indice_de_posicao(v / v.norm()).item()
    assert p(0, 0) == pytest.approx(1.0)
    # 30° acima (τ = 0): exp((35,2 − 1,22)·1e-3·30 + 21e-5·30²)
    assert p(30, 0) == pytest.approx(math.exp(33.98e-3 * 30 + 21e-5 * 900), rel=1e-6)
    # 30° abaixo: Iwata, 1 + 0,8·tg 30°
    assert p(-30, 0) == pytest.approx(1 + 0.8 * math.tan(math.radians(30)), rel=1e-6)
    # simétrico esquerda/direita e cresce com o ângulo
    assert p(0, 30) == pytest.approx(p(0, -30))
    assert p(0, 10) < p(0, 30) < p(0, 60)


def test_formula_dgp():
    assert dgp(torch.tensor(3000.0), torch.tensor(0.0), False).item() == pytest.approx(
        5.87e-5 * 3000 + 0.16)
    soma = 10_000.0**2 * 0.01 / 1.0
    esperado = 5.87e-5 * 2000 + 9.18e-2 * math.log10(1 + soma / 2000**1.87) + 0.16
    assert dgp(torch.tensor(2000.0), torch.tensor(soma), False).item() == pytest.approx(esperado)
    # correção de baixa luz só abaixo de 1000 lux
    g = dgp(torch.tensor(300.0), torch.tensor(0.0), False).item()
    x = math.exp(0.024 * 300 - 4)
    assert dgp(torch.tensor(300.0), torch.tensor(0.0), True).item() == pytest.approx(g * x / (1 + x))


def test_vista_horizontal_ceu_uniforme():
    # olhando para o horizonte, céu uniforme L, solo preto: Ev = π·L/2
    cena = Cena([retangulo(1e5, 1e5, 1e5 + 1, 1e5 + 1)])
    v = coeficientes_vistas(cena, mat(2), CEU, torch.zeros(1, 3), torch.tensor([[0, 1.0, 0]]),
                            n=96, amostras=64)
    L = ceu_uniforme(1000.0)
    lum = luminancia_pixels(v, L, L)
    ev, _ = avaliar_imagem(lum, v)
    assert ev.item() == pytest.approx(math.pi * 1000 / 2, rel=0.01)


def test_olhando_para_um_piso():
    # olhando para baixo a 1 m de um piso ρ = 0,5 sob céu uniforme: Ev = π·ρ·L
    cena = Cena([retangulo(-2000, -2000, 2000, 2000)])
    v = coeficientes_vistas(cena, mat(2, rho=0.5), CEU, torch.tensor([[0, 0, 1.0]]),
                            torch.tensor([[0, 0, -1.0]]), n=64, amostras=256)
    L = ceu_uniforme(1000.0)
    ev, _ = avaliar_imagem(luminancia_pixels(v, L, L), v)
    assert ev.item() == pytest.approx(math.pi * 0.5 * 1000, rel=0.02)


def test_sol_na_vista_e_atras():
    cena = Cena([retangulo(1e5, 1e5, 1e5 + 1, 1e5 + 1)])
    v = coeficientes_vistas(cena, mat(2), CEU, torch.zeros(1, 3), torch.tensor([[0, 1.0, 0]]),
                            n=32, amostras=4)
    lum = luminancia_pixels(v, ceu_uniforme(100.0), ceu_uniforme(100.0))
    ev0, g0 = avaliar_imagem(lum, v)
    # sol de 50 klux bem na frente: ofuscamento intolerável
    sol = (torch.tensor([[50_000.0]]), torch.tensor([[50_000.0]]), torch.tensor([[1.0]]))
    ev1, g1 = avaliar_imagem(lum, v, sol=sol)
    assert ev1.item() == pytest.approx(ev0.item() + 50_000)
    assert g1.item() > 0.9
    # sem sol na vista (iluminâncias zero): igual ao céu sozinho
    zero = (torch.zeros(1, 1), torch.zeros(1, 1), torch.ones(1, 1))
    assert avaliar_imagem(lum, v, sol=zero)[1].item() == pytest.approx(g0.item())
    assert OMEGA_SOL == pytest.approx(6.8e-5, rel=0.01)


def test_imagem_e_benchmark(capsys):
    # sala com parede norte de vidro; 8 vistas (2 posições × 4 direções)
    sv, sf = caixa(0, 0, 0, 6, 8, 3)
    rho = torch.full((sf.shape[0],), 0.5)
    tau = torch.zeros(sf.shape[0])
    tau[8:10] = 0.6
    cena = Cena([(sv, sf)])
    m = Materiais.de_listas([(rho, tau)])
    olhos = torch.tensor([[3.0, 4.0, 1.2]] * 4 + [[3.0, 1.5, 1.2]] * 4)
    frentes = torch.tensor([[0, 1.0, 0], [1.0, 0, 0], [0, -1.0, 0], [-1.0, 0, 0]] * 2)
    coeficientes_vistas(cena, m, CEU, olhos[:1], frentes[:1], n=16, amostras=2)  # compila
    torch.cuda.synchronize()
    t0 = time.perf_counter()
    v = coeficientes_vistas(cena, m, CEU, olhos, frentes, n=128, amostras=64, max_reflexoes=5)
    torch.cuda.synchronize()
    dt = time.perf_counter() - t0
    L = ceu_uniforme(3000.0, solo=0.2 * 3000)
    lum = luminancia_pixels(v, L, L)
    ev, g = avaliar_imagem(lum, v)
    img = imagem(v, lum[..., 0], vista=0)
    with capsys.disabled():
        print(f"\nolho-de-peixe: 8 vistas × {v.grade.uv.shape[0]} pixels × 64 amostras em "
              f"{dt * 1000:.0f} ms; Ev olhando a janela {ev[0, 0]:.0f} lux (DGP {g[0, 0]:.2f}), "
              f"de costas {ev[2, 0]:.0f} lux (DGP {g[2, 0]:.2f})")
    assert img.shape == (128, 128)
    assert ev[0, 0] > ev[2, 0]  # olhando a janela recebe mais
    assert g[0, 0] >= g[2, 0]

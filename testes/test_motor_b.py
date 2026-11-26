"""Motor B (luz natural com reflexões) contra resultados analíticos."""
import math
import time

import pytest
import torch

from otimiza.modulos.sol_luz.motor_b import (
    Materiais, absorcao_vidro, ase, coeficientes_luz, fator_luz_diurna, fator_sol_direto,
    horas_ocupadas, iluminancia_anual, metricas_anuais, sda,
)
from otimiza.nucleo.ceu import criar_ceu
from otimiza.nucleo.clima import ler_epw
from otimiza.nucleo.geometria import caixa, juntar, retangulo, sensores_de_malha
from otimiza.nucleo.matriz_ceu import eficacia_luminosa, matriz_ceu
from otimiza.nucleo.raios import Cena
from otimiza.nucleo.sol import posicao_solar_clima
from testes.conftest import EPWS_REAIS

CEU = criar_ceu(1)
CIMA = torch.tensor([[0, 0, 1.0]])
BAIXO = torch.tensor([[0, 0, -1.0]])


def materiais(n_faces, rho=0.0, tau=0.0):
    return Materiais.de_listas([(torch.full((n_faces,), rho), torch.full((n_faces,), tau))])


def cena_longe():
    v, f = retangulo(1e5, 1e5, 1e5 + 1, 1e5 + 1)
    return Cena([(v, f)]), materiais(f.shape[0])


def tau_python(cos_i, a0, n=1.52):
    si = math.sqrt(1 - cos_i**2); st = si / n; ct = math.sqrt(1 - st**2)
    rs = (cos_i - n * ct) / (cos_i + n * ct); rp = (n * cos_i - ct) / (n * cos_i + ct)
    r = 0.5 * (rs**2 + rp**2); a = a0 ** (1 / ct)
    return (1 - r) ** 2 * a / (1 - r**2 * a**2)


def test_vidro_transmitancia_normal():
    for tau in [0.4, 0.6, 0.85]:
        assert tau_python(1.0, absorcao_vidro(tau)) == pytest.approx(tau, rel=1e-9)
    # cai com o ângulo (Fresnel)
    a0 = absorcao_vidro(0.6)
    assert tau_python(math.cos(math.radians(60)), a0) < 0.6
    assert tau_python(math.cos(math.radians(85)), a0) < 0.3


def test_ceu_livre_coeficientes_iguais_omega_cos():
    cena, mat = cena_longe()
    c = coeficientes_luz(cena, mat, CEU, torch.zeros(1, 3), CIMA, n_raios=400_000)
    d = c.direto[0].double().cpu()
    assert d.sum().item() == pytest.approx(math.pi, rel=1e-3)
    assert d[-1].item() == 0  # olhando para cima não vê o solo
    esperado = CEU.angulo_solido * CEU.vetores[:, 2]
    assert torch.allclose(d[:-1], esperado, rtol=0.1, atol=2e-3)
    assert c.refletido.abs().max() == 0


def test_olhando_para_baixo_ve_o_solo():
    cena, mat = cena_longe()
    c = coeficientes_luz(cena, mat, CEU, torch.zeros(1, 3), BAIXO, n_raios=100_000)
    # soma de 100 mil parcelas em float32 (atomic_add): arredondamento ~0,1%
    assert c.direto[0, -1].item() == pytest.approx(math.pi, rel=3e-3)


def test_reflexao_de_um_piso_infinito():
    # sensor olhando para baixo a 1 m de um piso ρ = 0,5: E = π·ρ·L sob céu uniforme
    v, f = retangulo(-2000, -2000, 2000, 2000)
    cena, mat = Cena([(v, f)]), materiais(f.shape[0], rho=0.5)
    c = coeficientes_luz(cena, mat, CEU, torch.tensor([[0, 0, 1.0]]), BAIXO, n_raios=200_000)
    assert c.refletido[0, :-1].sum().item() == pytest.approx(0.5 * math.pi, rel=0.01)
    assert c.direto[0].sum().item() < 0.01  # quase nada passa pela borda do piso


def test_vidro_horizontal():
    v, f = retangulo(-1000, -1000, 1000, 1000, z=1.0)
    cena, mat = Cena([(v, f)]), materiais(f.shape[0], tau=0.6)
    c = coeficientes_luz(cena, mat, CEU, torch.zeros(1, 3), CIMA, n_raios=400_000)
    d = c.direto[0].double().cpu()
    calota = CEU.n_patches - 1
    assert d[calota].item() == pytest.approx(0.6 * CEU.angulo_solido[calota].item(), rel=0.06)
    # total < 0,6π porque τ cai em ângulos rasantes
    assert 0.4 * math.pi < d.sum().item() < 0.6 * math.pi


def test_sol_direto():
    sol = torch.tensor([[0, 0, 1.0], [0, math.sin(math.radians(30)), math.cos(math.radians(30))]])
    cena, mat = cena_longe()
    f = fator_sol_direto(cena, mat, torch.zeros(1, 3), CIMA, sol).cpu()
    assert torch.allclose(f[0], torch.tensor([1.0, math.cos(math.radians(30))]), atol=1e-6)
    v, fc = retangulo(-1000, -1000, 1000, 1000, z=1.0)
    opaco = fator_sol_direto(Cena([(v, fc)]), materiais(2), torch.zeros(1, 3), CIMA, sol).cpu()
    assert opaco.abs().max() == 0
    vidro = fator_sol_direto(Cena([(v, fc)]), materiais(2, tau=0.6), torch.zeros(1, 3), CIMA, sol).cpu()
    assert vidro[0, 0].item() == pytest.approx(0.6, rel=1e-4)
    a0 = absorcao_vidro(0.6)
    assert vidro[0, 1].item() == pytest.approx(tau_python(math.cos(math.radians(30)), a0)
                                               * math.cos(math.radians(30)), rel=1e-4)


def test_metricas():
    e = torch.tensor([[500.0] * 10, [50.0] * 10, [5000.0] * 5 + [200.0] * 5])
    occ = torch.ones(10, dtype=torch.bool)
    m = metricas_anuais(e, occ)
    assert m["da"].tolist() == [100, 0, 50]
    assert m["udi"].tolist() == [100, 0, 50]
    assert m["udi_baixo"].tolist() == [0, 100, 0]
    assert m["udi_alto"].tolist() == [0, 0, 50]
    assert sda(m["da"]) == pytest.approx(200 / 3)
    assert sda(m["da"], torch.tensor([1.0, 1.0, 2.0])) == pytest.approx(75.0)
    sol = torch.zeros(2, 400)
    sol[0, :260] = 2000
    assert ase(sol, torch.ones(400, dtype=torch.bool)) == pytest.approx(50.0)


def test_fator_de_luz_diurna_ceu_livre():
    cena, mat = cena_longe()
    c = coeficientes_luz(cena, mat, CEU, torch.zeros(1, 3), CIMA, n_raios=400_000)
    assert fator_luz_diurna(c, CEU).item() == pytest.approx(100.0, rel=0.01)


@pytest.mark.skipif(not EPWS_REAIS, reason="nenhum EPW real em data/epw")
def test_ceu_visivel_contra_iluminancias_do_epw():
    c = ler_epw(EPWS_REAIS[0])
    s = posicao_solar_clima(c)
    m = matriz_ceu(c, CEU, s, luz=True)
    dif = (m.difusa * (CEU.angulo_solido * CEU.vetores[:, 2])[:, None]).sum(0)
    _, k_dir = eficacia_luminosa(c, s)
    ok = (s.altitude > 0) & (c["ilum_difusa_horiz"] > 0) & (c["ilum_difusa_horiz"] < 999_000)
    if ok.sum() < 1000:
        pytest.skip("EPW sem iluminâncias")
    assert dif[ok].sum() / c["ilum_difusa_horiz"][ok].sum() == pytest.approx(1.0, abs=0.06)
    dn = c["dni"] * k_dir
    assert dn[ok].sum() / c["ilum_direta_normal"][ok].sum() == pytest.approx(1.0, abs=0.03)


@pytest.mark.skipif(not EPWS_REAIS, reason="nenhum EPW real em data/epw")
def test_iluminancia_anual_ceu_livre():
    c = ler_epw(EPWS_REAIS[0])
    s = posicao_solar_clima(c)
    m = matriz_ceu(c, CEU, s, luz=True)
    _, k_dir = eficacia_luminosa(c, s)
    cena, mat = cena_longe()
    coef = coeficientes_luz(cena, mat, CEU, torch.zeros(1, 3), CIMA, n_raios=200_000)
    horas = ((s.altitude > 0) & (c["dni"] > 0)).nonzero().flatten()
    fs = fator_sol_direto(cena, mat, torch.zeros(1, 3), CIMA, s.vetores[horas])
    e = iluminancia_anual(coef, m, fs, c["dni"] * k_dir, horas).double().cpu()[0]
    dif = (m.difusa * (CEU.angulo_solido * CEU.vetores[:, 2])[:, None]).sum(0)
    esperado = dif + c["dni"] * k_dir * torch.sin(torch.deg2rad(s.altitude)).clamp(min=0)
    assert e.sum().item() == pytest.approx(esperado.sum().item(), rel=0.01)
    occ = horas_ocupadas(c)
    assert occ.sum() == 3650


def test_benchmark_sala(capsys):
    # sala 6 x 8 x 3 m, janela de vidro na fachada norte, 192 sensores a 0,8 m
    sala_v, sala_f = caixa(0, 0, 0, 6, 8, 3)
    n = sala_f.shape[0]
    rho = torch.full((n,), 0.5)
    tau = torch.zeros(n)
    # parede norte (y = 8) inteira vira vidro: faces 8 e 9 da caixa
    tau[8:10] = 0.6
    cena = Cena([(sala_v, sala_f)])
    mat = Materiais.de_listas([(rho, tau)])
    s = sensores_de_malha(*retangulo(0.25, 0.25, 5.75, 7.75, z=0.8), espacamento=0.5)
    coeficientes_luz(cena, mat, CEU, s.pontos[:2], s.normais[:2], n_raios=64)  # compila
    torch.cuda.synchronize()
    t0 = time.perf_counter()
    c = coeficientes_luz(cena, mat, CEU, s.pontos, s.normais, n_raios=8192, max_reflexoes=5)
    torch.cuda.synchronize()
    dt = time.perf_counter() - t0
    df = fator_luz_diurna(c, CEU).cpu()
    with capsys.disabled():
        print(f"\nsala: {s.n} sensores × 8192 raios × até 5 reflexões em {dt * 1000:.0f} ms; "
              f"DF médio {df.mean():.2f}% (perto da janela {df[s.pontos[:, 1] > 7].mean():.1f}%, "
              f"fundo {df[s.pontos[:, 1] < 1].mean():.2f}%)")
    assert df[s.pontos[:, 1] > 7].mean() > df[s.pontos[:, 1] < 1].mean()
    assert (c.refletido.sum(1) > 0).all()

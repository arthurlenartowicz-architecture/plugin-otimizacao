"""1d: canal melanópico (m-EDI / EML) contra resultados analíticos."""
import math

import pytest
import torch

from otimiza.modulos.sol_luz.motor_b import (
    EML_POR_MEDI, Materiais, coeficientes_luz, eml, fator_sol_direto, iluminancia_anual,
    iluminancia_melanopica_anual, metricas_circadianas, sensores_olho,
)
from otimiza.nucleo.ceu import criar_ceu
from otimiza.nucleo.clima import ler_epw
from otimiza.nucleo.geometria import caixa, retangulo
from otimiza.nucleo.matriz_ceu import eficacia_luminosa, matriz_ceu
from otimiza.nucleo.raios import Cena
from otimiza.nucleo.sol import posicao_solar_clima
from testes.conftest import EPWS_REAIS

CEU = criar_ceu(1)
BAIXO = torch.tensor([[0, 0, -1.0]])


def test_piso_colorido_reflete_menos_no_canal_melanopico():
    # piso ρ_v = 0,5 e ρ_mel = 0,3 (ex.: piso de madeira, reflete pouco azul)
    v, f = retangulo(-2000, -2000, 2000, 2000)
    n = f.shape[0]
    mat = Materiais(torch.full((n,), 0.5), torch.zeros(n), torch.tensor([0], dtype=torch.int32),
                    refletancia_mel=torch.full((n,), 0.3))
    cena = Cena([(v, f)])
    p = torch.tensor([[0, 0, 1.0]])
    c_v = coeficientes_luz(cena, mat, CEU, p, BAIXO, n_raios=200_000)
    c_m = coeficientes_luz(cena, mat.melanopico(), CEU, p, BAIXO, n_raios=200_000)
    assert c_v.refletido[0].sum().item() == pytest.approx(0.5 * math.pi, rel=0.01)
    assert c_m.refletido[0].sum().item() == pytest.approx(0.3 * math.pi, rel=0.01)


def test_material_neutro_e_igual_nos_dois_canais():
    n = 4
    mat = Materiais(torch.full((n,), 0.5), torch.zeros(n), torch.tensor([0], dtype=torch.int32))
    m = mat.melanopico()
    assert torch.equal(m.refletancia, mat.refletancia)
    assert torch.equal(m.transmitancia, mat.transmitancia)


@pytest.mark.skipif(not EPWS_REAIS, reason="nenhum EPW real em data/epw")
def test_der_escala_ceu_e_sol_separadamente():
    c = ler_epw(EPWS_REAIS[0])
    s = posicao_solar_clima(c)
    m = matriz_ceu(c, CEU, s, luz=True)
    _, k_dir = eficacia_luminosa(c, s)
    v, f = retangulo(1e5, 1e5, 1e5 + 1, 1e5 + 1)
    cena = Cena([(v, f)])
    mat = Materiais.de_listas([(torch.zeros(2), torch.zeros(2))])
    p, nrm = torch.zeros(1, 3), torch.tensor([[0, 0, 1.0]])
    coef = coeficientes_luz(cena, mat, CEU, p, nrm, n_raios=200_000)
    horas = ((s.altitude > 0) & (c["dni"] > 0)).nonzero().flatten()
    fs = fator_sol_direto(cena, mat, p, nrm, s.vetores[horas])
    e_sol = c["dni"] * k_dir
    foto = iluminancia_anual(coef, m, fs, e_sol, horas).double().cpu()[0]
    # neutro e DER = 1: m-EDI = lux
    mel1 = iluminancia_melanopica_anual(coef, m, fs, e_sol, horas).double().cpu()[0]
    assert torch.allclose(mel1, foto, rtol=1e-5)
    # DER diferentes: m-EDI = DER_céu · (parte difusa) + DER_sol · (parte do sol)
    so_sol = iluminancia_anual(coef, m, fs, e_sol, horas, so_sol_direto=True).double().cpu()[0]
    mel = iluminancia_melanopica_anual(coef, m, fs, e_sol, horas, der_ceu=1.2, der_sol=0.9)
    mel = mel.double().cpu()[0]
    esperado = 1.2 * (foto - so_sol) + 0.9 * so_sol  # sensor livre: sem sol por reflexão
    assert mel.sum().item() == pytest.approx(esperado.sum().item(), rel=1e-4)


def test_eml():
    assert eml(torch.tensor([100.0])).item() == pytest.approx(100 * EML_POR_MEDI)


def test_sensores_olho():
    pts, nrm, origem = sensores_olho(torch.tensor([[1.0, 2.0], [3.0, 4.0]]), altura=1.2)
    assert pts.shape == (8, 3) and nrm.shape == (8, 3)
    assert torch.allclose(pts[:, 2], torch.full((8,), 1.2, dtype=torch.float64))
    assert torch.allclose(nrm[:, 2], torch.zeros(8, dtype=torch.float64))
    assert torch.allclose(nrm.norm(dim=1), torch.ones(8, dtype=torch.float64))
    assert torch.allclose(nrm[0], torch.tensor([0, 1.0, 0], dtype=torch.float64), atol=1e-12)  # norte
    assert origem.tolist() == [0, 0, 0, 0, 1, 1, 1, 1]


@pytest.mark.skipif(not EPWS_REAIS, reason="nenhum EPW real em data/epw")
def test_metricas_circadianas():
    c = ler_epw(EPWS_REAIS[0])
    e = torch.zeros(2, 8760)
    hora = c.hora_meio
    # sensor 0: 300 lux das 9 às 13 h todos os dias (4 h); sensor 1: só 3 h
    e[0, (hora >= 9) & (hora < 13)] = 300
    e[1, (hora >= 9) & (hora < 12)] = 300
    m = metricas_circadianas(e, c)
    assert m["dias_pct"].tolist() == [100.0, 0.0]
    assert m["horas_pct"][0].item() == pytest.approx(4 / 10 * 100)

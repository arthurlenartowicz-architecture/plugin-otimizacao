import pytest
import torch

from otimiza.nucleo.ceu import criar_ceu
from otimiza.nucleo.clima import Clima, Local, ler_epw
from otimiza.nucleo.matriz_ceu import COEF_PEREZ, matriz_ceu
from otimiza.nucleo.sol import posicao_solar
from testes.conftest import EPWS_REAIS

LOCAL_SP = Local("Teste", "SP", "BRA", "", "", -23.55, -46.63, -3.0, 760.0)


def clima_horas(mes, dia, hora, dni, dhi) -> Clima:
    """Clima pequeno, só com as horas pedidas (para testes)."""
    t = lambda v: torch.as_tensor(v, dtype=torch.float64)
    return Clima(
        local=LOCAL_SP,
        mes=torch.as_tensor(mes), dia=torch.as_tensor(dia), hora=torch.as_tensor(hora),
        dados={"dni": t(dni), "dhi": t(dhi)},
    )


def calcular(c, mf=1):
    ceu = criar_ceu(mf)
    sol = posicao_solar(LOCAL_SP.latitude, LOCAL_SP.longitude, LOCAL_SP.fuso,
                        c.mes, c.dia, c.hora_meio)
    return matriz_ceu(c, ceu, sol), ceu, sol


def test_tabela_perez():
    t = torch.tensor(COEF_PEREZ)
    assert t.shape == (8, 5, 4)
    assert t[0, 0, 0] == pytest.approx(1.3525)   # a1 da faixa 1
    assert t[7, 4, 3] == pytest.approx(0.5636)   # e4 da faixa 8


@pytest.mark.parametrize("mf", [1, 2])
def test_conserva_difusa_horizontal_e_direta_normal(mf):
    # várias condições: encoberto, parcial e limpo, ao longo do dia
    c = clima_horas([3, 3, 3, 6, 12], [21, 21, 21, 21, 21], [9, 12, 16, 13, 11],
                    dni=[0, 300, 800, 650, 900], dhi=[250, 200, 90, 100, 120])
    m, ceu, _ = calcular(c, mf)
    om, cz = ceu.angulo_solido, ceu.vetores[:, 2]
    dif_horiz = (m.difusa * (om * cz)[:, None]).sum(0)
    dir_normal = (m.direta * om[:, None]).sum(0)
    assert torch.allclose(dif_horiz, c["dhi"], rtol=1e-10)
    assert torch.allclose(dir_normal, c["dni"], rtol=1e-10)


def test_noite_e_zero():
    c = clima_horas([6], [21], [2], dni=[0], dhi=[0])
    m, _, _ = calcular(c)
    assert m.total.abs().max() == 0


def test_ceu_encoberto_mais_claro_no_zenite():
    c = clima_horas([6], [21], [13], dni=[0], dhi=[150])
    m, ceu, _ = calcular(c)
    L = m.difusa[:, 0]
    zenite = L[-1]
    horizonte = L[ceu.faixa == 0].mean()
    assert zenite > 1.5 * horizonte


def test_ceu_limpo_brilha_perto_do_sol():
    c = clima_horas([12], [21], [11], dni=[900], dhi=[90])
    m, ceu, sol = calcular(c)
    L = m.difusa[:, 0]
    perto_do_sol = (ceu.vetores @ sol.vetores[0]).argmax()
    assert L.argmax() == perto_do_sol  # brilho circunsolar
    # direta concentrada nos 4 patches mais próximos
    assert (m.direta[:, 0] > 0).sum() == 4


@pytest.mark.skipif(not EPWS_REAIS, reason="nenhum EPW real em data/epw")
def test_ano_real():
    c = ler_epw(EPWS_REAIS[0])
    ceu = criar_ceu(1)
    m = matriz_ceu(c, ceu)
    assert m.difusa.shape == (145, 8760)
    assert (m.total >= 0).all()
    # difusa horizontal anual = soma da DHI das horas com sol acima do horizonte
    om, cz = ceu.angulo_solido, ceu.vetores[:, 2]
    dif_anual = (m.difusa * (om * cz)[:, None]).sum() / 1000
    from otimiza.nucleo.sol import posicao_solar_clima
    dia = posicao_solar_clima(c).altitude > 0
    assert dif_anual.item() == pytest.approx(c["dhi"][dia].sum().item() / 1000, rel=1e-9)
    print(f"\n{c.local.cidade}: céu {m.acumulado_kwh().sum():.0f} kWh/m² somando os patches")

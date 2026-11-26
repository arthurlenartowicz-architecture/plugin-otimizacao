"""1b: motor A contra LB Incident Radiation e LB Direct Sun Hours.

Cena exportada pelo .gh (prédio 10x10x20 m + piso 60x60 m, torre ao norte).
Pulados até existirem os arquivos em testes/referencias/<caso>/.
"""
import pytest
import torch

from otimiza.modulos.sol_luz.motor_a import CeuDirecional, ceu_direcional, horas_de_sol, radiacao_incidente
from otimiza.nucleo.ceu import criar_ceu
from otimiza.nucleo.clima import ler_epw
from otimiza.nucleo.geometria import juntar, ler_malha_json, sensores_encostados
from otimiza.nucleo.matriz_ceu import matriz_ceu
from otimiza.nucleo.raios import Cena
from otimiza.nucleo.sol import posicao_solar
from testes.conftest import PASTA_EPW
from testes.referencias import carregar, casos_com

NECESSARIOS = ["geometria.json", "contexto.json", "sensores_lb.json"]
CASOS = [c for c in casos_com("sensores_lb.json") if all((c / a).is_file() for a in NECESSARIOS)]
pytestmark = pytest.mark.skipif(not CASOS, reason="estudo da 1b não exportado")


def preparar(caso):
    geo = ler_malha_json(caso / "geometria.json")
    ctx = ler_malha_json(caso / "contexto.json")
    v, f = juntar(geo, ctx)
    cena = Cena([(v, f)])
    s = carregar(caso, "sensores_lb.json")
    n = torch.tensor(s["normais"], dtype=torch.float64)
    c = torch.tensor(s["centros"], dtype=torch.float64)
    # sensores encostados em outra superfície: ambíguos (o próprio LB dá valores
    # diferentes para pontos em situação idêntica); ficam fora da comparação
    validos = ~sensores_encostados(c, n, v, f)
    return cena, c + s["deslocamento"] * n, n, validos


def resumo(nos, lb, nome, validos):
    ok = validos & (lb > 50)  # ignora sensores quase sem radiação (divisão instável)
    rel = (nos[ok] - lb[ok]) / lb[ok]
    txt = (f"{nome}: {int(ok.sum())} sensores ({int((~validos).sum())} encostados fora), "
           f"erro relativo médio {rel.abs().mean():.4f}, "
           f"p99 {rel.abs().quantile(0.99):.4f}, máx {rel.abs().max():.4f}; "
           f"total nos/LB {nos.sum() / lb.sum():.4f}")
    print("\n" + txt)
    return rel


def lb_valores(caso, nome):
    return torch.tensor(carregar(caso, f"resultado_{nome}.json")["valores"], dtype=torch.float64)


@pytest.mark.parametrize("caso", CASOS, ids=lambda p: p.name)
def test_radiacao_geometria_com_ceu_do_lb(caso):
    """Isola o raycasting: mesmos sensores, mesmas direções e mesma energia por patch."""
    if not (caso / "resultado_radiacao_sem_solo.json").is_file():
        pytest.skip("sem resultado_radiacao_sem_solo.json")
    cena, p, n, validos = preparar(caso)
    mtx = carregar(caso, "matriz_ceu_ladybug_0.json")
    e = torch.tensor(mtx["direct_values"], dtype=torch.float64) + \
        torch.tensor(mtx["diffuse_values"], dtype=torch.float64)
    v_lb = torch.tensor(carregar(caso, "ceu_ladybug.json")["tregenza"], dtype=torch.float64)
    nos = radiacao_incidente(cena, p, n, CeuDirecional(v_lb, e, 145)).double().cpu()
    lb = lb_valores(caso, "radiacao_sem_solo")
    assert nos.shape == lb.shape
    rel = resumo(nos, lb, "raycasting (direções e céu do LB)", validos)
    # mesmo cálculo: idêntico (< 0,01%) na grande maioria; poucos sensores em
    # diagonais exatas, onde um raio raspa a aresta de um canto, divergem até ~4%
    assert (rel.abs() < 1e-4).double().mean() > 0.9
    assert rel.abs().max() < 0.05


@pytest.mark.parametrize("caso", CASOS, ids=lambda p: p.name)
def test_radiacao_nossas_direcoes_ceu_do_lb(caso):
    """Nossas direções de raio (centro de área) com a energia de patch do LB."""
    if not (caso / "resultado_radiacao_sem_solo.json").is_file():
        pytest.skip("sem resultado_radiacao_sem_solo.json")
    cena, p, n, validos = preparar(caso)
    mtx = carregar(caso, "matriz_ceu_ladybug_0.json")
    e = torch.tensor(mtx["direct_values"], dtype=torch.float64) + \
        torch.tensor(mtx["diffuse_values"], dtype=torch.float64)
    nos = radiacao_incidente(cena, p, n, CeuDirecional(criar_ceu(1).vetores_raio, e, 145)).double().cpu()
    rel = resumo(nos, lb_valores(caso, "radiacao_sem_solo"), "nossas direções, céu do LB", validos)
    assert rel.abs().mean() < 0.005
    assert rel.abs().max() < 0.05


@pytest.mark.parametrize("caso", CASOS, ids=lambda p: p.name)
def test_radiacao_completa_sem_solo(caso):
    if not (caso / "resultado_radiacao_sem_solo.json").is_file():
        pytest.skip("sem resultado_radiacao_sem_solo.json")
    cena, p, n, validos = preparar(caso)
    clima = ler_epw(sorted(PASTA_EPW.rglob("*.epw"))[0])
    ceu = ceu_direcional(matriz_ceu(clima, criar_ceu(1)), refletancia_solo=0.0)
    nos = radiacao_incidente(cena, p, n, ceu).double().cpu()
    rel = resumo(nos, lb_valores(caso, "radiacao_sem_solo"), "motor A sem solo", validos)
    assert rel.abs().mean() < 0.03
    assert rel.abs().quantile(0.99) < 0.06


@pytest.mark.parametrize("caso", CASOS, ids=lambda p: p.name)
def test_radiacao_completa_com_solo(caso):
    if not (caso / "resultado_radiacao.json").is_file():
        pytest.skip("sem resultado_radiacao.json")
    cena, p, n, validos = preparar(caso)
    clima = ler_epw(sorted(PASTA_EPW.rglob("*.epw"))[0])
    ceu = ceu_direcional(matriz_ceu(clima, criar_ceu(1)), refletancia_solo=0.2, modelo_solo="ladybug")
    nos = radiacao_incidente(cena, p, n, ceu).double().cpu()
    rel = resumo(nos, lb_valores(caso, "radiacao"), "motor A com solo 0,2 (modelo LB)", validos)
    assert rel.abs().mean() < 0.03
    assert rel.abs().quantile(0.99) < 0.06


@pytest.mark.parametrize("caso", CASOS, ids=lambda p: p.name)
def test_vetores_sol_iguais_aos_nossos(caso):
    if not (caso / "sol_vetores_lb.json").is_file():
        pytest.skip("sem sol_vetores_lb.json")
    d = carregar(caso, "sol_vetores_lb.json")
    ref = carregar(caso, "sol_ladybug.json")
    v_lb = -torch.tensor(d["vetores"], dtype=torch.float64)  # LB aponta do sol para o chão
    hoy = torch.tensor(d["hoys"], dtype=torch.float64)
    dia = torch.floor(hoy / 24)
    s = posicao_solar(ref["latitude"], ref["longitude"], ref["fuso"], 1, 1 + dia, hoy - 24 * dia)
    ang = torch.rad2deg(torch.acos((s.vetores * v_lb).sum(1).clamp(-1, 1)))
    print(f"\nvetores de sol: {len(hoy)} horas, ângulo máx. {ang.max():.4f}°")
    assert ang.max() < 0.05


@pytest.mark.parametrize("caso", CASOS, ids=lambda p: p.name)
def test_horas_de_sol(caso):
    if not ((caso / "resultado_horas_sol.json").is_file() and (caso / "sol_vetores_lb.json").is_file()):
        pytest.skip("sem horas_sol / vetores de sol")
    cena, p, n, validos = preparar(caso)
    vs = -torch.tensor(carregar(caso, "sol_vetores_lb.json")["vetores"], dtype=torch.float64)
    nos = horas_de_sol(cena, p, n, vs).double().cpu()
    lb = lb_valores(caso, "horas_sol")
    dif = (nos - lb).abs()[validos]
    print(f"\nhoras de sol: {int(validos.sum())} sensores ({int((~validos).sum())} encostados fora), "
          f"iguais {(dif == 0).double().mean():.3%}, dif. média {dif.mean():.3f} h, "
          f"máx {dif.max():.0f} h (de {vs.shape[0]} h de sol)")
    assert (dif == 0).double().mean() > 0.99
    assert dif.max() <= 2

"""1e: motor C (DGP) contra HB Imageless Annual Glare (dcglare).

Mesma sala da 1c, grade de 8 vistas a 1,2 m. Pulados até existir
testes/referencias/<caso>/sala_hb_ofusc.json.
Medido em Curitiba: GA dif. média 1,5 ponto (máx 3,6); DGP hora a hora
(8 × 4431 h) |dif| média 0,011, correlação 0,979, 91% na mesma classe.
O dcglare aplica a correção de baixa luz (sem ela: |dif| 0,027, corr. 0,936).
"""
import pytest
import torch

from otimiza.modulos.sol_luz.motor_b import horas_ocupadas
from otimiza.modulos.sol_luz.motor_c import autonomia_ofuscamento, coeficientes_vistas, dgp_anual
from otimiza.nucleo.ceu import criar_ceu
from otimiza.nucleo.clima import ler_epw
from otimiza.nucleo.matriz_ceu import eficacia_luminosa, matriz_ceu
from otimiza.nucleo.sol import posicao_solar_clima
from testes.conftest import PASTA_EPW
from testes.referencias import carregar, casos_com
from testes.test_honeybee_1c import carregar_sala

CASOS = casos_com("sala_hb_ofusc.json")
pytestmark = pytest.mark.skipif(not CASOS, reason="vistas do Honeybee não exportadas")


def calcular(caso, correcao_baixa_luz=True):
    cena, mat, olho, frente, _ = carregar_sala(caso, "sala_hb_ofusc.json")
    clima = ler_epw(sorted(PASTA_EPW.rglob("*.epw"))[0])
    sol = posicao_solar_clima(clima)
    ceu = criar_ceu(2)
    m = matriz_ceu(clima, ceu, sol, luz=True)
    _, k_dir = eficacia_luminosa(clima, sol)
    v = coeficientes_vistas(cena, mat, ceu, olho, frente, n=128, amostras=64, max_reflexoes=5)
    horas = horas_ocupadas(clima).nonzero().flatten()
    ev, g = dgp_anual(cena, mat, v, m, sol.vetores, clima["dni"] * k_dir, horas,
                      correcao_baixa_luz=correcao_baixa_luz)
    return ev.cpu(), g.cpu(), horas


@pytest.mark.parametrize("caso", CASOS, ids=lambda p: p.name)
def test_arquivos_de_resultado(caso):
    if not (caso / "hb_ofusc_arquivos.json").is_file():
        pytest.skip("sem hb_ofusc_arquivos.json")
    arqs = carregar(caso, "hb_ofusc_arquivos.json")["arquivos"]
    for nome, txt in arqs.items():
        print(f"\n  {nome}: {len(txt)} caracteres, início: {txt[:120]!r}", end="")
    assert arqs


@pytest.mark.parametrize("caso", CASOS, ids=lambda p: p.name)
def test_autonomia_de_ofuscamento(caso):
    if not (caso / "resultado_hb_ga.json").is_file():
        pytest.skip("sem resultado_hb_ga.json")
    ref = torch.tensor(carregar(caso, "resultado_hb_ga.json")["valores"], dtype=torch.float64)
    ev, g, _ = calcular(caso)
    ga = autonomia_ofuscamento(g).double()
    dif = (ga - ref).abs()
    print(f"\nGA nós {[round(x, 1) for x in ga.tolist()]}"
          f"\n   HB {[round(x, 1) for x in ref.tolist()]}; dif. média {dif.mean():.2f} pontos")
    assert dif.mean() < 3.0
    assert dif.max() < 6.0


@pytest.mark.parametrize("caso", CASOS, ids=lambda p: p.name)
def test_dgp_hora_a_hora(caso):
    if not (caso / "hb_ofusc_arquivos.json").is_file():
        pytest.skip("sem hb_ofusc_arquivos.json")
    arq = carregar(caso, "hb_ofusc_arquivos.json")["arquivos"]
    nome_dgp = next(n for n in arq if n.endswith(".dgp"))
    hoys = torch.tensor([float(x) for x in arq["sun-up-hours.txt"].split()])
    ref = torch.tensor([[float(x) for x in l.split()] for l in arq[nome_dgp].strip().splitlines()],
                       dtype=torch.float64)
    cena, mat, olho, frente, _ = carregar_sala(caso, "sala_hb_ofusc.json")
    clima = ler_epw(sorted(PASTA_EPW.rglob("*.epw"))[0])
    sol = posicao_solar_clima(clima)
    ceu = criar_ceu(2)
    m = matriz_ceu(clima, ceu, sol, luz=True)
    _, k_dir = eficacia_luminosa(clima, sol)
    v = coeficientes_vistas(cena, mat, ceu, olho, frente, n=128, amostras=64, max_reflexoes=5)
    horas = torch.floor(hoys).long()  # HB usa o meio da hora (6,5 -> linha 6 do EPW)
    _, g = dgp_anual(cena, mat, v, m, sol.vetores, clima["dni"] * k_dir, horas)
    g = g.double().cpu()
    d = (g - ref).abs()
    corr = torch.corrcoef(torch.stack([g.flatten(), ref.flatten()]))[0, 1]
    classe = lambda x: torch.bucketize(x, torch.tensor([0.35, 0.40, 0.45], dtype=torch.float64))
    mesma = (classe(g) == classe(ref)).double().mean()
    print(f"\nDGP {tuple(ref.shape)}: |dif| média {d.mean():.4f}, p95 {d.flatten().quantile(0.95):.3f}, "
          f"correlação {corr:.4f}, mesma classe {mesma:.1%}")
    assert d.mean() < 0.02
    assert d.flatten().quantile(0.95) < 0.08
    assert corr > 0.97
    assert mesma > 0.88

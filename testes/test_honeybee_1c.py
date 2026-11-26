"""1c: motor B contra Honeybee-Radiance (sala de teste montada pelo .gh).

Usa a geometria, os materiais e os sensores do próprio modelo HB exportado.
Pulados até existir testes/referencias/<caso>/sala_hb.json.
Medido em Curitiba (sala 6x8x3 m, janelas 40%, materiais padrão do HB, -ab 5):
DF médio −2,7% (erro por sensor médio 2,7%, máx 9%); DA dif. média 0,08 ponto;
UDI 0,9 ponto (máx ~9 em sensores que passam muitas horas perto de 3000 lux).
"""
import pytest
import torch

from otimiza.modulos.sol_luz.motor_b import (
    Materiais, coeficientes_luz, fator_luz_diurna, fator_sol_direto, horas_ocupadas,
    iluminancia_anual, metricas_anuais,
)
from otimiza.nucleo.ceu import criar_ceu
from otimiza.nucleo.clima import ler_epw
from otimiza.nucleo.matriz_ceu import eficacia_luminosa, matriz_ceu
from otimiza.nucleo.raios import Cena
from otimiza.nucleo.sol import posicao_solar_clima
from testes.conftest import PASTA_EPW
from testes.referencias import carregar, casos_com

CASOS = casos_com("sala_hb.json")
pytestmark = pytest.mark.skipif(not CASOS, reason="sala do Honeybee não exportada")
N_RAIOS = 16384
REFLEXOES = 5  # igual ao -ab 5 usado no Honeybee


def carregar_sala(caso, arquivo="sala_hb.json"):
    d = carregar(caso, arquivo)
    vs, fs, rho, tau, base = [], [], [], [], 0
    for s in d["superficies"]:
        v = torch.tensor(s["vertices"], dtype=torch.float64)
        f = torch.tensor(s["faces"], dtype=torch.int64)
        vidro = s["classe"] == "Glass"
        vs.append(v)
        fs.append(f + base)
        base += v.shape[0]
        n = f.shape[0]
        rho.append(torch.full((n,), 0.0 if vidro else float(s["refletancia"] or 0.0)))
        tau.append(torch.full((n,), float(s["transmitancia"] or 0.0) if vidro else 0.0))
    cena = Cena([(torch.cat(vs), torch.cat(fs))])
    mat = Materiais.de_listas([(torch.cat(rho), torch.cat(tau))])
    pos = torch.tensor([s["pos"] for s in d["sensores"]], dtype=torch.float64)
    nrm = torch.tensor([s["dir"] for s in d["sensores"]], dtype=torch.float64)
    return cena, mat, pos, nrm, d


def hb(caso, nome):
    return torch.tensor(carregar(caso, f"resultado_{nome}.json")["valores"], dtype=torch.float64)


@pytest.mark.parametrize("caso", CASOS, ids=lambda p: p.name)
def test_materiais_da_sala(caso):
    *_, d = carregar_sala(caso)
    for s in d["superficies"]:
        print(f"\n  {s['tipo']:9s} {s['classe']:8s} ρ={s['refletancia']} τ={s['transmitancia']} "
              f"({s['modificador']})", end="")
    assert any(s["classe"] == "Glass" for s in d["superficies"])


@pytest.mark.parametrize("caso", CASOS, ids=lambda p: p.name)
def test_fator_de_luz_diurna(caso):
    if not (caso / "resultado_hb_df.json").is_file():
        pytest.skip("sem resultado_hb_df.json")
    cena, mat, pos, nrm, _ = carregar_sala(caso)
    ceu = criar_ceu(2)
    c = coeficientes_luz(cena, mat, ceu, pos, nrm, n_raios=N_RAIOS, max_reflexoes=REFLEXOES)
    nos = fator_luz_diurna(c, ceu).double().cpu()
    ref = hb(caso, "hb_df")
    assert nos.shape == ref.shape
    rel = (nos - ref) / ref.clamp(min=0.1)
    print(f"\nDF: {len(ref)} sensores, médio nós {nos.mean():.3f}% HB {ref.mean():.3f}% "
          f"(razão {nos.mean() / ref.mean():.3f}); erro rel. médio {rel.abs().mean():.3f}, "
          f"máx {rel.abs().max():.3f}")
    assert abs(nos.mean() / ref.mean() - 1) < 0.05
    assert rel.abs().mean() < 0.05
    assert rel.abs().max() < 0.15


@pytest.mark.parametrize("caso", CASOS, ids=lambda p: p.name)
def test_metricas_anuais(caso):
    if not (caso / "resultado_hb_da.json").is_file():
        pytest.skip("sem resultado_hb_da.json")
    cena, mat, pos, nrm, _ = carregar_sala(caso)
    clima = ler_epw(sorted(PASTA_EPW.rglob("*.epw"))[0])
    sol = posicao_solar_clima(clima)
    ceu = criar_ceu(2)
    m = matriz_ceu(clima, ceu, sol, luz=True)
    _, k_dir = eficacia_luminosa(clima, sol)
    c = coeficientes_luz(cena, mat, ceu, pos, nrm, n_raios=N_RAIOS, max_reflexoes=REFLEXOES)
    horas = ((sol.altitude > 0) & (clima["dni"] > 0)).nonzero().flatten()
    fs = fator_sol_direto(cena, mat, pos, nrm, sol.vetores[horas])
    e = iluminancia_anual(c, m, fs, clima["dni"] * k_dir, horas)
    nos = {k: v.double().cpu() for k, v in metricas_anuais(e, horas_ocupadas(clima)).items()}
    for nome, chave in [("da", "hb_da"), ("udi", "hb_udi"), ("udi_baixo", "hb_udi_baixo"),
                        ("udi_alto", "hb_udi_alto")]:
        if not (caso / f"resultado_{chave}.json").is_file():
            continue
        ref = hb(caso, chave)
        dif = (nos[nome] - ref).abs()
        print(f"\n{nome}: médio nós {nos[nome].mean():.1f}% HB {ref.mean():.1f}%, "
              f"dif. média {dif.mean():.2f} pontos, máx {dif.max():.1f}")
        assert dif.mean() < 2.0
        assert dif.max() < 15.0

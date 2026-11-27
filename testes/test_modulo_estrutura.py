"""Módulo estrutura no contrato comum + benchmark de variantes por segundo."""
import time

import pytest
import torch

import otimiza.modulos.estrutura  # noqa: F401  (registra o módulo)
from otimiza.modulos.estrutura.fem import resolver, tubo_circular
from otimiza.modulos.estrutura.prototipos import portico_espacial, trelica_pratt
from otimiza.nucleo.cena import CenaLote
from otimiza.nucleo.contrato import modulo
from otimiza.nucleo.motor import Problema, avaliar, avaliar_problema


def cena_estrutura(estruturas):
    return CenaLote(variantes=[], clima=None, estruturas=estruturas)


def test_trelica_pratt_isostatica():
    """Biapoiada com cargas simétricas: reação = carga total / 2, flecha finita."""
    s = trelica_pratt(gravidade=0)
    r = resolver([s], detalhes=True)
    assert bool(r.estavel[0])
    n = r.esforcos[0][:, 0, 0]
    # montante do apoio (barra 2n) recebe a reação inteira: compressão = 5 × 10 / 2
    assert abs(float(n[12]) + 25.0) < 1e-6


def test_modulo_no_motor():
    alturas = [1.0, 1.5, 2.0, 3.0]
    cena = cena_estrutura([trelica_pratt(altura=h) for h in alturas])
    chaves = [f"estrutura.{m}" for m in modulo("estrutura").metricas]
    r = avaliar(cena, chaves, nivel="padrao")
    for c in chaves:
        assert r[c].shape == (4,) and torch.isfinite(r[c]).all(), c
    d = r["estrutura.deslocamento_max"]
    assert (d[1:] < d[:-1]).all()           # treliça mais alta = mais rígida
    assert (r["estrutura.massa"][1:] > r["estrutura.massa"][:-1]).all()
    p = Problema(["estrutura.massa", "estrutura.deslocamento_max"],
                 restricoes=[("estrutura.utilizacao", "<=", 100.0)])
    f, viol = avaliar_problema(cena, p)
    assert f.shape == (4, 2) and viol.shape == (4,)


def test_niveis_mudam_flambagem():
    cena = cena_estrutura([portico_espacial(n_pav=2)])
    fr = avaliar(cena, ["estrutura.flambagem"], nivel="rapido")["estrutura.flambagem"]
    fv = avaliar(cena, ["estrutura.flambagem"], nivel="validacao")["estrutura.flambagem"]
    assert 0 < float(fv[0]) <= float(fr[0]) * 1.0001    # mais elementos → fator menor ou igual
    assert abs(float(fr[0]) / float(fv[0]) - 1) < 0.05


def test_sem_estruturas_da_erro():
    with pytest.raises(ValueError):
        avaliar(CenaLote(variantes=[], clima=None, estruturas=[]), ["estrutura.massa"])


@pytest.mark.skipif(not torch.cuda.is_available(), reason="benchmark só na GPU")
def test_benchmark(capsys):
    casos = {
        "pórtico 3 pav. (16 nós)": lambda i: portico_espacial(vao_x=5 + i % 7 * 0.3, n_pav=3,
                                                              pilar=tubo_circular(0.15 + i % 5 * 0.02, 0.01)),
        "treliça 20 painéis (42 nós)": lambda i: trelica_pratt(altura=1 + i % 9 * 0.2, n_paineis=20),
    }
    linhas = []
    for nome, gerar in casos.items():
        vs = [gerar(i) for i in range(256)]
        for nivel, flamb in (("rapido", False), ("padrao", False), ("padrao", True)):
            cena = cena_estrutura(vs)
            chaves = ["estrutura.massa", "estrutura.deslocamento_max", "estrutura.utilizacao"]
            if flamb:
                chaves.append("estrutura.flambagem")
            avaliar(cena, chaves, nivel)  # aquece
            torch.cuda.synchronize()
            t = time.perf_counter()
            avaliar(cena, chaves, nivel)
            torch.cuda.synchronize()
            dt = time.perf_counter() - t
            linhas.append(f"{nome:28s} {nivel:8s} flambagem={flamb!s:5s} {len(vs) / dt:8.0f} variantes/s")
    with capsys.disabled():
        print("\n" + "\n".join(linhas))

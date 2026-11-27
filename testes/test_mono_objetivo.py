"""Otimização com 1 objetivo (4f1): GA (padrão do `auto`) e CMA-ES (só contínuos).

Funções de teste com ótimo conhecido (esfera e Rastrigin deslocados: o ótimo não fica
no centro do intervalo, onde u = 0,5 favoreceria o CMA-ES), restrição com ótimo na
borda, e a treliça Pratt com 1 objetivo contra a busca exaustiva.
"""
import numpy as np
import pytest

import otimiza.modulos.estrutura  # noqa: F401  (registra o módulo)
from otimiza.modulos.estrutura.prototipos import trelica_pratt_de
from otimiza.nucleo.motor import Problema
from otimiza.nucleo.parametros import Categorico, Continuo, Espaco, Inteiro
from otimiza.otimizacao import otimizar, otimizar_problema
from otimiza.otimizacao.otimizador import Sessao, avaliador_motor
from otimiza.otimizacao.pareto import exportar
from testes.test_otimizacao import ESPACO_TRELICA

LIM = 5.12                      # domínio clássico do Rastrigin
C = 1.234                       # ótimo deslocado (x* = C em todas as coordenadas)


def _espaco(n):
    return Espaco([Continuo(f"x{i}", -LIM, LIM) for i in range(n)])


def _X(params):
    n = len(params[0])
    return np.array([[p[f"x{i}"] for i in range(n)] for p in params])


def esfera(params):
    X = _X(params) - C
    return (X ** 2).sum(1, keepdims=True), np.zeros(len(X))


def rastrigin(params):
    X = _X(params) - C
    f = 10 * X.shape[1] + (X ** 2 - 10 * np.cos(2 * np.pi * X)).sum(1)
    return f[:, None], np.zeros(len(X))


def _melhor(r):
    i = r.melhores(1)[0]
    return r.F[i, 0], _X([r.parametros[i]])[0]


@pytest.mark.parametrize("alg,tol", [("ga", 1e-2), ("cmaes", 1e-8)])
def test_esfera_10d(alg, tol):
    r = otimizar(_espaco(10), esfera, ["f"], algoritmo=alg, populacao=100, geracoes=200)
    f, x = _melhor(r)
    print(f"\nesfera 10D {alg}: f = {f:.2e}, |x - x*| máx {np.abs(x - C).max():.2e}, "
          f"{len(r.parametros)} avaliações, {r.tempo:.2f} s")
    assert r.algoritmo == alg
    assert f < tol


@pytest.mark.parametrize("alg", ["ga", "cmaes"])
def test_rastrigin_2d_global(alg):
    """2D: ótimo global (f = 0) entre ~100 mínimos locais."""
    r = otimizar(_espaco(2), rastrigin, ["f"], algoritmo=alg, populacao=50, geracoes=60)
    f, x = _melhor(r)
    print(f"\nRastrigin 2D {alg}: f = {f:.2e}, x = {x.round(4)}")
    assert f < 1e-3 and np.abs(x - C).max() < 0.01


@pytest.mark.parametrize("alg", ["ga", "cmaes"])
def test_rastrigin_5d_contra_aleatoria(alg):
    """5D, 3 sementes: muito melhor que a busca aleatória com o mesmo orçamento (10 mil
    pontos); pelo menos uma no ótimo global e nenhuma além do vale vizinho (f = 1,99 é uma
    coordenada a um vale do ótimo: o CMA-ES sem reinícios às vezes para aí)."""
    pop, ger = 100, 100
    fs = [_melhor(otimizar(_espaco(5), rastrigin, ["f"], algoritmo=alg, populacao=pop,
                           geracoes=ger, semente=s))[0] for s in range(3)]
    rng = np.random.default_rng(0)
    U = rng.random((pop * ger, 5))
    aleat = rastrigin(_espaco(5).decodificar(U))[0].min()
    print(f"\nRastrigin 5D {alg}: melhores {np.round(fs, 4)} × aleatória {aleat:.2f}")
    assert min(fs) < 0.01 and max(fs) < 2.5 and np.mean(fs) < 0.1 * aleat


@pytest.mark.parametrize("alg", ["ga", "cmaes"])
def test_restricao_ativa(alg):
    """min Σ(x - C)² com x0 ≥ 3: ótimo na borda (x0 = 3, o resto = C), f* = (3 - C)²."""
    def f(params):
        F, _ = esfera(params)
        x0 = _X(params)[:, 0]
        return F, np.maximum(0.0, 3.0 - x0)

    r = otimizar(_espaco(4), f, ["f"], algoritmo=alg, populacao=60, geracoes=150)
    fb, x = _melhor(r)
    print(f"\nrestrição {alg}: f = {fb:.5f} (f* = {(3 - C) ** 2:.5f}), x0 = {x[0]:.4f}, "
          f"{(~r.viavel).mean():.0%} inviáveis")
    assert r.viavel[r.melhores(1)].all() and x[0] >= 3.0
    assert fb == pytest.approx((3 - C) ** 2, rel=0.02)


def test_auto_e_erros():
    r = otimizar(_espaco(2), esfera, ["f"], populacao=10, geracoes=2)
    assert r.algoritmo == "ga"
    misto = Espaco([Continuo("a", 0, 1), Inteiro("n", 1, 5)])
    with pytest.raises(ValueError, match="contínuos"):
        otimizar(misto, lambda p: (np.zeros((len(p), 1)), np.zeros(len(p))), ["f"],
                 algoritmo="cmaes")
    with pytest.raises(ValueError, match="1 objetivo"):
        otimizar(_espaco(2), lambda p: (np.zeros((len(p), 2)), np.zeros(len(p))),
                 ["f", "g"], algoritmo="ga")


@pytest.mark.parametrize("alg", ["ga", "cmaes"])
def test_sessao_igual_ao_otimizar(alg):
    """O servidor usa a Sessao (ask/tell): mesma semente = mesmo resultado."""
    r = otimizar(_espaco(3), rastrigin, ["f"], algoritmo=alg, populacao=20, geracoes=8,
                 semente=3)
    s = Sessao(_espaco(3), ["f"], algoritmo=alg, populacao=20, geracoes=8, semente=3)
    while (p := s.pedir()) is not None:
        s.responder(*rastrigin(p))
    r2 = s.resultado()
    assert np.array_equal(r.F, r2.F) and len(r.parametros) == len(r2.parametros)


# ---------------------------------------------------------------- caso real: treliça
# massa mínima com utilização ≤ 100% e flecha ≤ L/250 = 4,8 cm (vão de 12 m)
PROBLEMA_MASSA = Problema(["estrutura.massa"],
                          restricoes=[("estrutura.utilizacao", "<=", 100),
                                      ("estrutura.deslocamento_max", "<=", 4.8)])


def test_trelica_massa_contra_busca_exaustiva(tmp_path):
    """GA com 400 avaliações (735 na grade) chega à massa mínima da busca exaustiva
    (ou abaixo: a altura é contínua no GA e tem 15 valores na grade)."""
    av = avaliador_motor(trelica_pratt_de, PROBLEMA_MASSA, "rapido")
    grade = ESPACO_TRELICA.grade(15)
    Fg, vg = av(grade)
    m_grade = Fg[vg <= 0, 0].min()
    r = otimizar_problema(ESPACO_TRELICA, trelica_pratt_de, PROBLEMA_MASSA, "rapido",
                          populacao=40, geracoes=10)
    assert r.algoritmo == "ga"
    i = r.melhores(1)[0]
    print(f"\ntreliça massa: GA {r.objetivos[i, 0]:.1f} kg × grade {m_grade:.1f} kg "
          f"({len(r.parametros)} × {len(grade)} avaliações), {r.parametros[i]}")
    assert r.F[i, 0] <= 1.01 * m_grade
    # reavaliar o melhor dá o mesmo valor e respeita as restrições
    F2, v2 = av([r.parametros[i]])
    assert np.allclose(F2, r.F[i], rtol=1e-5) and (v2 <= 0).all()
    # exportar com 1 objetivo: os 20 melhores distintos, do melhor ao pior
    dados = exportar(r, str(tmp_path), k_clusters=3)
    massas = [s["objetivos"]["estrutura.massa"] for s in dados["solucoes"]]
    assert len(massas) == 20 and massas == sorted(massas) and massas[0] == r.objetivos[i, 0]
    assert len({str(s["parametros"]) for s in dados["solucoes"]}) == 20

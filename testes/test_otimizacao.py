"""Etapa 3: parâmetros, otimizador (NSGA-II, NSGA-III, RVEA) e frente de Pareto.

Validação em problemas com frente de Pareto conhecida (ZDT, DTLZ2) pelo IGD, e
num caso real (treliça massa × deslocamento) contra a busca exaustiva.
"""
import json

import numpy as np
import pytest
from pymoo.problems import get_problem
from pymoo.util.ref_dirs import get_reference_directions

import otimiza.modulos.estrutura  # noqa: F401  (registra o módulo)
from otimiza.modulos.estrutura.prototipos import TUBOS, trelica_pratt_de
from otimiza.nucleo.motor import Problema
from otimiza.nucleo.parametros import Categorico, Continuo, Espaco, Inteiro, cena_de
from otimiza.otimizacao import otimizar, otimizar_problema
from otimiza.otimizacao.otimizador import VIOLACAO_NAN, avaliador_motor
from otimiza.otimizacao.pareto import (clusters, exportar, hipervolume, igd, nao_dominados)
from testes.conftest import EPWS_REAIS


# ---------------------------------------------------------------- parâmetros
def test_decodificar_tipos():
    esp = Espaco([Continuo("a", 2, 4), Inteiro("n", 3, 6), Categorico("c", ("x", "y", "z"))])
    p = esp.decodificar([[0.5, 0.0, 0.99], [1.0, 1.0, 0.0], [0.0, 0.26, 0.5]])
    assert p[0] == {"a": 3.0, "n": 3, "c": "z"}
    assert p[1] == {"a": 4.0, "n": 6, "c": "x"}
    assert p[2] == {"a": 2.0, "n": 4, "c": "y"}
    assert type(p[0]["n"]) is int and type(p[0]["a"]) is float


def test_inteiro_faixas_iguais():
    """Cada valor inteiro recebe a mesma fatia de [0, 1] (sorteio sem viés)."""
    esp = Espaco([Inteiro("n", 1, 4)])
    u = np.linspace(0, 1, 40001)[:, None]
    vals = [d["n"] for d in esp.decodificar(u)]
    contagem = np.bincount(vals)[1:]
    assert np.allclose(contagem / contagem.sum(), 0.25, atol=1e-3)


def test_codificar_arredondar_e_grade():
    esp = Espaco([Continuo("a", 0, 10), Inteiro("n", 0, 4), Categorico("c", ("p", "q"))])
    v = {"a": 2.5, "n": 3, "c": "q"}
    assert esp.decodificar(esp.codificar(v))[0] == v
    U = esp.arredondar(np.array([[0.25, 0.61, 0.51], [0.25, 0.79, 0.99]]))
    assert np.allclose(U[0], U[1])                      # mesmos valores → mesmo vetor
    assert len(esp.grade(3)) == 3 * 5 * 2
    with pytest.raises(ValueError):
        Espaco([Continuo("a", 0, 1), Inteiro("a", 0, 1)])


def test_cena_de_estrutura():
    esp = Espaco([Continuo("altura", 1, 2), Inteiro("n_paineis", 4, 6),
                  Categorico("secao", tuple(TUBOS))])
    cena = cena_de(trelica_pratt_de, esp.decodificar(np.random.default_rng(0).random((5, 3))))
    assert cena.n == 5 and len(cena.estruturas) == 5


# ---------------------------------------------------------------- referências analíticas
def _avaliador_pymoo(prob):
    n = prob.n_var

    def f(params):
        X = np.array([[p[f"x{i}"] for i in range(n)] for p in params])
        return prob.evaluate(X), np.zeros(len(X))
    return f


def _espaco(n):
    return Espaco([Continuo(f"x{i}", 0, 1) for i in range(n)])


@pytest.mark.parametrize("nome", ["zdt1", "zdt2", "zdt3"])
def test_nsga2_zdt(nome):
    """ZDT (2 objetivos, 30 variáveis): frente conhecida; IGD do arquivo e da população."""
    prob = get_problem(nome)
    r = otimizar(_espaco(30), _avaliador_pymoo(prob), ["f1", "f2"], populacao=100, geracoes=200)
    assert r.algoritmo == "nsga2"
    assert r.info["avaliacoes"] == len(r.parametros) == 100 * 200
    frente = r.frente()
    pf = prob.pareto_front()
    assert igd(r.F[frente], pf) < 0.005
    assert igd(r.F_final, pf) < 0.02
    print(f"\n{nome}: IGD arquivo {igd(r.F[frente], pf):.4f}, população {igd(r.F_final, pf):.4f}, "
          f"{r.tempo:.2f} s")


def _frente_dtlz2(m, particoes):
    pf = get_reference_directions("das-dennis", m, n_partitions=particoes)
    return pf / np.linalg.norm(pf, axis=1, keepdims=True)   # frente = esfera unitária


@pytest.mark.parametrize("m,algoritmo,esperado", [(3, "nsga2", "nsga2"), (3, "auto", "nsga3"),
                                                  (5, "auto", "nsga3"), (5, "rvea", "rvea")])
def test_dtlz2(m, algoritmo, esperado):
    """DTLZ2: frente = parte positiva da esfera unitária. Convergência = |‖F‖ − 1|;
    cobertura = IGD comparado ao melhor possível com aquele nº de pontos (as próprias
    direções de referência projetadas na esfera)."""
    pop, ger, particoes = (92, 250, 12) if m == 3 else (126, 300, 6)
    prob = get_problem("dtlz2", n_var=m + 9, n_obj=m)
    r = otimizar(_espaco(m + 9), _avaliador_pymoo(prob), [f"f{j}" for j in range(m)],
                 algoritmo=algoritmo, populacao=pop, geracoes=ger)
    assert r.algoritmo == esperado
    pf = _frente_dtlz2(m, particoes)
    dist = np.abs(np.linalg.norm(r.F_final, axis=1) - 1).mean()
    ideal = get_reference_directions("energy", m, pop, seed=0)
    ideal = igd(ideal / np.linalg.norm(ideal, axis=1, keepdims=True), pf)
    val = igd(r.F_final, pf)
    print(f"\nDTLZ2 {m} obj {esperado}: dist. à esfera {dist:.4f}, IGD {val:.4f} "
          f"(ideal {ideal:.4f}), {r.tempo:.2f} s")
    assert dist < 0.01
    if esperado == "nsga2":
        assert val < 0.1          # NSGA-II espalha pior com 3 objetivos (aglomeração)
    else:
        assert val < ideal + 0.01


def test_nan_vira_inviavel():
    """Variantes com NaN (ex.: estrutura instável) ficam inviáveis e fora da frente."""
    prob = get_problem("zdt1", n_var=10)
    base = _avaliador_pymoo(prob)

    def f(params):
        F, v = base(params)
        ruim = np.array([p["x0"] > 0.8 for p in params])
        F[ruim, 1] = np.nan
        return F, v

    r = otimizar(_espaco(10), f, ["f1", "f2"], populacao=40, geracoes=40)
    ruins = np.array([p["x0"] > 0.8 for p in r.parametros])
    assert ruins.any() and (r.violacao[ruins] == VIOLACAO_NAN).all()
    frente = r.frente()
    assert len(frente) > 10 and not ruins[frente].any()
    assert np.isfinite(r.F[frente]).all()


def test_nao_dominados_e_clusters():
    F = np.array([[0, 3], [1, 1], [3, 0], [2, 2], [1, 1.5], [0.5, 2]])
    assert list(nao_dominados(F)) == [0, 1, 2, 5]
    rng = np.random.default_rng(0)
    grupos = np.concatenate([rng.normal(c, 0.02, (20, 2)) for c in ([0, 1], [0.5, 0.5], [1, 0])])
    rot, reps = clusters(grupos, 3)
    assert len(reps) == 3 and len(set(rot[:20])) == len(set(rot[20:40])) == 1
    assert list(rot[[0, 20, 40]]) == [0, 1, 2]           # numerados pelo 1º objetivo
    assert all(rot[r] == i for i, r in enumerate(reps))


# ---------------------------------------------------------------- caso real: treliça
ESPACO_TRELICA = Espaco([Continuo("altura", 0.6, 3.0), Inteiro("n_paineis", 4, 10),
                         Categorico("secao", tuple(TUBOS))])
PROBLEMA_TRELICA = Problema(["estrutura.massa", "estrutura.deslocamento_max"],
                            restricoes=[("estrutura.utilizacao", "<=", 100)])


def test_trelica_contra_busca_exaustiva():
    """Pratt 12 m: massa × flecha com utilização ≤ 100%. O NSGA-II, com ~1/2 das
    avaliações da grade, deve chegar ao hipervolume da busca exaustiva (15 alturas ×
    7 nº de painéis × 7 tubos = 735 projetos)."""
    av = avaliador_motor(trelica_pratt_de, PROBLEMA_TRELICA, "rapido")
    grade = ESPACO_TRELICA.grade(15)
    Fg, vg = av(grade)
    Fv = Fg[vg <= 0]
    ref = Fv.max(axis=0) * 1.1
    hv_grade = hipervolume(Fv[nao_dominados(Fv)], ref)
    r = otimizar_problema(ESPACO_TRELICA, trelica_pratt_de, PROBLEMA_TRELICA, "rapido",
                          populacao=40, geracoes=10)
    frente = r.frente()
    hv = hipervolume(r.F[frente], ref)
    print(f"\ntreliça: HV NSGA-II / grade = {hv / hv_grade:.4f} ({len(r.parametros)} avaliações "
          f"× {len(grade)} da grade), {len(frente)} na frente, {r.tempo:.2f} s")
    assert hv >= 0.99 * hv_grade
    # a frente respeita a restrição e está nas unidades originais (kg, cm)
    assert r.viavel[frente].all() and (r.objetivos[frente] > 0).all()
    # conferência independente: reavaliar a frente dá os mesmos valores
    F2, v2 = av([r.parametros[i] for i in frente])
    assert np.allclose(F2, r.F[frente], rtol=1e-5) and (v2 <= 0).all()


def test_exportar(tmp_path):
    r = otimizar_problema(ESPACO_TRELICA, trelica_pratt_de, PROBLEMA_TRELICA, "rapido",
                          populacao=20, geracoes=5)
    dados = exportar(r, str(tmp_path), gerador=trelica_pratt_de, k_clusters=3, max_solucoes=15)
    lido = json.loads((tmp_path / "resultado.json").read_text(encoding="utf-8"))
    assert lido == dados
    assert (tmp_path / "paralelas.png").stat().st_size > 1000
    sol = lido["solucoes"]
    assert 1 < len(sol) <= 15 and sum(s["representante"] for s in sol) <= 3
    s = sol[0]
    assert set(s["parametros"]) == {"altura", "n_paineis", "secao"}
    assert set(s["objetivos"]) == {"estrutura.massa", "estrutura.deslocamento_max"}
    g = s["geometria"]
    assert g["tipo"] == "barras" and len(g["barras"]) > 0 and len(g["nos"][0]) == 3
    assert [p["tipo"] for p in lido["parametros"]] == ["continuo", "inteiro", "categorico"]


# ---------------------------------------------------------------- caso real: sol_luz
@pytest.mark.skipif(not EPWS_REAIS, reason="nenhum EPW real em data/epw")
def test_sol_luz_rapido():
    """Sala 6×8 m: janelas norte e sul + rotação, sDA (max) × ASE (min), nível rápido."""
    import otimiza.modulos.sol_luz  # noqa: F401
    from otimiza.nucleo.clima import ler_epw
    from otimiza.nucleo.prototipos import predio_de

    esp = Espaco([Continuo("janela_N", 0.1, 0.8), Continuo("janela_S", 0.0, 0.6),
                  Continuo("rotacao_graus", -45, 45)])
    prob = Problema(["sol_luz.sda", "sol_luz.ase"])
    r = otimizar_problema(esp, predio_de, prob, "rapido", clima=ler_epw(EPWS_REAIS[0]),
                          populacao=16, geracoes=4)
    frente = r.frente()
    print(f"\nsol_luz: {len(r.parametros)} avaliações em {r.tempo:.2f} s, {len(frente)} na frente")
    assert r.sentidos == ["max", "min"] and len(frente) >= 1
    obj = r.objetivos[frente]
    assert np.isfinite(obj).all() and (obj >= 0).all() and (obj <= 100.01).all()   # %; float32

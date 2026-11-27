"""Progresso por geração (4f2, GET /progresso): histórico e frente ao vivo.

A frente do Progresso tem de ser a mesma da `Resultado.frente()` (ou `melhores(20)` com
1 objetivo) a qualquer momento, e o hipervolume (normalização fixa) nunca diminui.
"""
import json

import numpy as np
import pytest
from pymoo.problems import get_problem

from otimiza.nucleo.parametros import Continuo, Espaco
from otimiza.otimizacao.otimizador import Sessao
from otimiza.otimizacao.pareto import hipervolume
from otimiza.otimizacao.progresso import Progresso


def _rodar(prob, m, algoritmo="auto", pop=40, ger=25, nan=None):
    n = prob.n_var
    esp = Espaco([Continuo(f"x{i}", 0, 1) for i in range(n)])
    s = Sessao(esp, [f"f{j}" for j in range(m)], algoritmo=algoritmo, populacao=pop, geracoes=ger)
    pg = Progresso(s.nomes, s.sentidos)
    frentes = []
    while (p := s.pedir()) is not None:
        X = np.array([[q[f"x{i}"] for i in range(n)] for q in p])
        F = prob.evaluate(X).reshape(len(p), m)
        if nan is not None:
            F[nan(X)] = np.nan
        s.responder(F, np.zeros(len(p)))
        pg.registrar(*s.problema.registro[-1])
        frentes.append((s.resultado(), pg.indices_frente().copy()))
    return s, pg, frentes


def _chaves(r, idx):
    return {json.dumps(r.parametros[i], sort_keys=True) for i in idx}


def test_zdt1_hipervolume_e_frente():
    s, pg, frentes = _rodar(get_problem("zdt1", n_var=10), 2)
    hv = [h["hipervolume"] for h in pg.historico]
    assert len(hv) == 25 and all(b >= a - 1e-12 for a, b in zip(hv, hv[1:])) and hv[-1] > hv[0]
    for r, idx in frentes:                     # a cada geração, a mesma frente do Resultado
        assert _chaves(r, idx) == _chaves(r, r.frente())
    # o mesmo hipervolume calculado à parte com a normalização da 1ª geração
    r = s.resultado()
    F0 = r.F[r.geracao == 0]
    lo, hi = F0.min(0), F0.max(0)
    ref_hv = hipervolume((r.F[r.frente()] - lo) / (hi - lo), np.full(2, 1.1))
    assert hv[-1] == pytest.approx(ref_hv, rel=1e-9)
    # melhor de cada objetivo nunca piora
    for j in range(2):
        b = [h["melhor"][j] for h in pg.historico]
        assert all(y <= x + 1e-12 for x, y in zip(b, b[1:]))
    d = pg.dict(desde=20)
    assert [h["geracao"] for h in d["historico"]] == [20, 21, 22, 23, 24]
    assert len(d["frente"]) == d["n_frente"] and {f["cluster"] for f in d["frente"]} <= set(range(5))


def test_4_objetivos_monte_carlo_sem_diminuir():
    _, pg, _ = _rodar(get_problem("dtlz2", n_var=8, n_obj=4), 4, pop=40, ger=15)
    hv = [h["hipervolume"] for h in pg.historico]
    assert all(b >= a for a, b in zip(hv, hv[1:])) and hv[-1] > 0


def test_um_objetivo_melhores_e_sentido_original():
    class Esfera:
        n_var = 3

        def evaluate(self, X):
            return ((X - 0.3) ** 2).sum(1, keepdims=True)

    s, pg, frentes = _rodar(Esfera(), 1, pop=20, ger=10)
    for r, idx in frentes:
        assert list(idx) == list(r.melhores(20))
    b = [h["melhor"][0] for h in pg.historico]
    assert all(y <= x for x, y in zip(b, b[1:])) and pg.historico[0]["hipervolume"] is None
    # sentido max volta positivo (objetivos e melhor no sentido original)
    pg2 = Progresso(["a"], ["max"])
    pg2.registrar([{"x": 1}, {"x": 2}], [[-3.0], [-5.0]], [0, 0])
    assert pg2.historico[0]["melhor"] == [5.0] and pg2.frente()[0]["objetivos"] == [5.0]


def test_inviaveis_e_nan_ficam_fora():
    pg = Progresso(["a", "b"], ["min", "min"])
    pg.registrar([{"x": 1}, {"x": 2}], [[1, 2], [2, 1]], [1.0, 0.5])        # nenhum viável
    assert pg.historico[0]["hipervolume"] == 0.0 and pg.historico[0]["n_frente"] == 0
    pg.registrar([{"x": 3}, {"x": 4}, {"x": 3}], [[1, 1], [np.nan, 0], [1, 1]], [0, 0, 0])
    assert pg.historico[1]["n_frente"] == 1 and pg.historico[1]["viaveis"] == 2
    assert [f["parametros"] for f in pg.frente()] == [{"x": 3}]

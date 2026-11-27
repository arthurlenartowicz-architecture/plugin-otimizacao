"""Otimização multiobjetivo com a população inteira avaliada em lote.

Algoritmos (pymoo): NSGA-II para até 3 objetivos; NSGA-III (ou RVEA) para 4 ou
mais, guiados por direções de referência. O pymoo cuida da seleção, cruzamento
(SBX) e mutação (polinomial); a avaliação de cada geração é uma só chamada ao
`avaliador`, que recebe a lista de parâmetros da população toda.

Variantes com NaN/inf (estrutura instável, variante sem sensores) viram
inviáveis com violação alta: perdem para qualquer viável e não travam a busca.
Todas as avaliações ficam guardadas (`Resultado.historico`); a frente de Pareto
é tirada desse histórico, não só da última população.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Sequence

import numpy as np
from pymoo.core.problem import Problem
from pymoo.core.repair import Repair
from pymoo.optimize import minimize

from otimiza.nucleo.parametros import Espaco, cena_de

VIOLACAO_NAN = 1e6   # violação dada a variantes que deram NaN/inf
F_NAN = 1e12         # valor dos objetivos dessas variantes (finito, para o pymoo)

# avaliador(lista de dicts de parâmetros) -> (F (N, m) para minimizar, violação (N,))
Avaliador = Callable[[list[dict]], tuple[np.ndarray, np.ndarray]]


@dataclass
class Resultado:
    espaco: Espaco
    nomes: list[str]                  # nomes dos objetivos
    sentidos: list[str]               # "min"/"max" de cada objetivo
    parametros: list[dict]            # todas as avaliações, na ordem
    F: np.ndarray                     # (N, m) no sentido de minimização
    violacao: np.ndarray              # (N,) 0 = viável
    geracao: np.ndarray               # (N,) geração em que foi avaliada
    tempo: float = 0.0
    algoritmo: str = ""
    F_final: np.ndarray | None = None  # população final do algoritmo (sentido de minimização)
    info: dict = field(default_factory=dict)

    @property
    def objetivos(self) -> np.ndarray:
        """Valores nas unidades e sentidos originais (max volta positivo)."""
        sinal = np.array([1.0 if s == "min" else -1.0 for s in self.sentidos])
        return self.F * sinal

    @property
    def viavel(self) -> np.ndarray:
        return self.violacao <= 0

    def frente(self) -> np.ndarray:
        """Índices (no histórico) das soluções viáveis não dominadas, sem repetidas."""
        from otimiza.otimizacao.pareto import nao_dominados
        idx = np.flatnonzero(self.viavel)
        if idx.size == 0:
            return idx
        # remove parâmetros repetidos (mesmo projeto avaliado mais de uma vez)
        vistos, unicos = set(), []
        for i in idx:
            chave = tuple(sorted((k, str(v)) for k, v in self.parametros[i].items()))
            if chave not in vistos:
                vistos.add(chave)
                unicos.append(i)
        unicos = np.array(unicos)
        return unicos[nao_dominados(self.F[unicos])]


class _Arredondar(Repair):
    def __init__(self, espaco: Espaco):
        super().__init__()
        self.espaco = espaco

    def _do(self, problem, X, **kwargs):
        return self.espaco.arredondar(X)


class _ProblemaLote(Problem):
    def __init__(self, espaco: Espaco, avaliador: Avaliador, n_obj: int):
        super().__init__(n_var=espaco.n, n_obj=n_obj, n_ieq_constr=1, xl=0.0, xu=1.0)
        self.espaco, self.avaliador = espaco, avaliador
        self.registro: list[tuple[list[dict], np.ndarray, np.ndarray]] = []

    def _evaluate(self, X, out, *args, **kwargs):
        params = self.espaco.decodificar(X)
        F, viol = self.avaliador(params)
        F = np.array(F, dtype=float).reshape(len(params), self.n_obj)
        viol = np.array(viol, dtype=float).reshape(len(params))
        ruim = ~np.isfinite(F).all(axis=1) | ~np.isfinite(viol)
        F[ruim] = F_NAN
        viol[ruim] = VIOLACAO_NAN
        self.registro.append((params, F.copy(), viol.copy()))
        out["F"] = F
        out["G"] = viol[:, None]


def criar_algoritmo(nome: str, n_obj: int, populacao: int, espaco: Espaco, semente: int = 0):
    from pymoo.algorithms.moo.nsga2 import NSGA2
    from pymoo.algorithms.moo.nsga3 import NSGA3
    from pymoo.algorithms.moo.rvea import RVEA
    from pymoo.util.ref_dirs import get_reference_directions

    if nome == "auto":
        nome = "nsga2" if n_obj <= 3 else "nsga3"
    reparo = _Arredondar(espaco)
    if nome == "nsga2":
        return nome, NSGA2(pop_size=populacao, repair=reparo, eliminate_duplicates=True)
    if n_obj < 2:
        raise ValueError(f"{nome} precisa de 2 ou mais objetivos")
    # direções de referência espalhadas por "energia" (qualquer quantidade, ≈ população)
    ref = get_reference_directions("energy", n_obj, populacao, seed=semente)
    if nome == "nsga3":
        return nome, NSGA3(ref_dirs=ref, pop_size=populacao, repair=reparo,
                           eliminate_duplicates=True)
    if nome == "rvea":
        return nome, RVEA(ref_dirs=ref, pop_size=populacao, repair=reparo,
                          eliminate_duplicates=True)
    raise ValueError(f"algoritmo desconhecido: {nome!r} (use auto, nsga2, nsga3 ou rvea)")


def otimizar(espaco: Espaco, avaliador: Avaliador, nomes: Sequence[str],
             sentidos: Sequence[str] | None = None, algoritmo: str = "auto",
             populacao: int = 100, geracoes: int = 100, semente: int = 0,
             verbose: bool = False) -> Resultado:
    """Roda o algoritmo e devolve todas as avaliações (ver `Resultado.frente`)."""
    nomes = list(nomes)
    n_obj = len(nomes)
    sentidos = list(sentidos) if sentidos else ["min"] * n_obj
    nome_alg, alg = criar_algoritmo(algoritmo, n_obj, populacao, espaco, semente)
    prob = _ProblemaLote(espaco, avaliador, n_obj)
    t0 = time.perf_counter()
    res = minimize(prob, alg, ("n_gen", geracoes), seed=semente, verbose=verbose)
    tempo = time.perf_counter() - t0
    params, Fs, viols, gens = [], [], [], []
    for g, (p, f, v) in enumerate(prob.registro):
        params += p
        Fs.append(f)
        viols.append(v)
        gens.append(np.full(len(p), g))
    return Resultado(espaco, nomes, sentidos, params, np.concatenate(Fs), np.concatenate(viols),
                     np.concatenate(gens), tempo, nome_alg, res.pop.get("F"),
                     dict(populacao=populacao, geracoes=geracoes, semente=semente,
                          avaliacoes=len(params)))


def avaliador_motor(gerador: Callable[[dict], object], problema, nivel: str = "padrao",
                    clima=None, cache=None, device=None) -> Avaliador:
    """Avaliador que monta a CenaLote da população e chama `motor.avaliar_problema`.
    O cache (sol e matrizes de céu do clima) é o mesmo em todas as gerações."""
    from otimiza.nucleo.cache import Cache
    from otimiza.nucleo.motor import avaliar_problema

    cache = cache if cache is not None else Cache()

    def avaliar(params: list[dict]):
        cena = cena_de(gerador, params, clima=clima, device=device)
        cache.resultados.clear()   # resultados são por cena; o resto do cache continua
        F, viol = avaliar_problema(cena, problema, nivel, cache)
        return F.detach().double().cpu().numpy(), viol.detach().double().cpu().numpy()

    return avaliar


def otimizar_problema(espaco: Espaco, gerador, problema, nivel: str = "padrao", clima=None,
                      **kw) -> Resultado:
    """Atalho: otimiza um `motor.Problema` ("modulo.metrica") com o gerador dado."""
    return otimizar(espaco, avaliador_motor(gerador, problema, nivel, clima),
                    problema.objetivos, [problema.sentido(o) for o in problema.objetivos], **kw)

"""Otimização multiobjetivo com a população inteira avaliada em lote.

Algoritmos (pymoo): NSGA-II para 2 objetivos; NSGA-III (ou RVEA) para 3 ou
mais, guiados por direções de referência (com 3 objetivos o NSGA-II espalha
mal a frente: IGD 0,077 contra 0,009 do NSGA-III no DTLZ2). O pymoo cuida da seleção, cruzamento
(SBX) e mutação (polinomial); a avaliação de cada geração é uma só chamada ao
`avaliador`, que recebe a lista de parâmetros da população toda.

Variantes com NaN/inf (estrutura instável, variante sem sensores) viram
inviáveis com violação alta: perdem para qualquer viável e não travam a busca.
Todas as avaliações ficam guardadas (`Resultado.historico`); a frente de Pareto
é tirada desse histórico, não só da última população.
"""
from __future__ import annotations

import copy
import time
from dataclasses import dataclass, field
from typing import Callable, Sequence

import numpy as np
from pymoo.core.problem import Problem
from pymoo.core.repair import Repair

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
        nome = "nsga2" if n_obj <= 2 else "nsga3"
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


class Sessao:
    """Otimização passo a passo (ask/tell), para quando quem avalia está fora do Python
    (ex.: o Grasshopper gera a geometria; ver `otimiza.servidor`).

        s = Sessao(espaco, nomes, sentidos)
        while (params := s.pedir()) is not None:
            s.responder(*avaliar(params))
        r = s.resultado()

    Mesma sequência do `pymoo.optimize.minimize` (infill → avaliação → advance): com a
    mesma semente, dá o mesmo resultado do `otimizar`."""

    def __init__(self, espaco: Espaco, nomes: Sequence[str], sentidos: Sequence[str] | None = None,
                 algoritmo: str = "auto", populacao: int = 100, geracoes: int = 100,
                 semente: int = 0, verbose: bool = False):
        self.espaco = espaco
        self.nomes = list(nomes)
        n_obj = len(self.nomes)
        self.sentidos = list(sentidos) if sentidos else ["min"] * n_obj
        self.nome_alg, alg = criar_algoritmo(algoritmo, n_obj, populacao, espaco, semente)
        self._pendente: tuple | None = None
        self.problema = _ProblemaLote(espaco, lambda p: self._pendente, n_obj)
        self.alg = copy.deepcopy(alg)
        self.alg.setup(self.problema, termination=("n_gen", geracoes), seed=semente, verbose=verbose)
        self.info = dict(populacao=populacao, geracoes=geracoes, semente=semente)
        self._infills = None
        self._t0 = time.perf_counter()
        self.tempo = 0.0

    @property
    def geracao(self) -> int:
        """Nº de lotes já respondidos (0 = população inicial)."""
        return len(self.problema.registro)

    def pedir(self) -> list[dict] | None:
        """Parâmetros do próximo lote (o mesmo lote até ser respondido); None = acabou."""
        if self._infills is None:
            while self.alg.has_next():
                pop = self.alg.infill()
                if pop is not None and len(pop):
                    self._infills = pop
                    break
                self.alg.advance()       # como no pymoo: sem infill, só avança
            else:
                self.tempo = time.perf_counter() - self._t0
                return None
        return self.espaco.decodificar(self._infills.get("X"))

    def responder(self, F, violacao) -> None:
        """Objetivos (N, m) para minimizar e violação (N,) do lote pedido."""
        if self._infills is None:
            raise RuntimeError("responder sem pedir antes")
        self._pendente = (F, violacao)
        self.alg.evaluator.eval(self.problema, self._infills, algorithm=self.alg)
        self.alg.advance(infills=self._infills)
        self._infills, self._pendente = None, None
        self.tempo = time.perf_counter() - self._t0

    def resultado(self) -> Resultado:
        """Todas as avaliações feitas até agora (pode chamar antes do fim)."""
        params, Fs, viols, gens = [], [], [], []
        for g, (p, f, v) in enumerate(self.problema.registro):
            params += p
            Fs.append(f)
            viols.append(v)
            gens.append(np.full(len(p), g))
        m = len(self.nomes)
        cat = (lambda xs, forma: np.concatenate(xs) if xs else np.zeros(forma))
        pop = self.alg.pop
        return Resultado(self.espaco, self.nomes, self.sentidos, params, cat(Fs, (0, m)),
                         cat(viols, (0,)), cat(gens, (0,)).astype(int), self.tempo, self.nome_alg,
                         None if pop is None else pop.get("F"),
                         dict(self.info, avaliacoes=len(params)))


def otimizar(espaco: Espaco, avaliador: Avaliador, nomes: Sequence[str],
             sentidos: Sequence[str] | None = None, algoritmo: str = "auto",
             populacao: int = 100, geracoes: int = 100, semente: int = 0,
             verbose: bool = False) -> Resultado:
    """Roda o algoritmo e devolve todas as avaliações (ver `Resultado.frente`)."""
    s = Sessao(espaco, nomes, sentidos, algoritmo, populacao, geracoes, semente, verbose)
    while (params := s.pedir()) is not None:
        s.responder(*avaliador(params))
    return s.resultado()


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

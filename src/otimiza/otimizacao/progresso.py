"""Progresso de uma otimização, geração a geração (para os gráficos ao vivo da janela).

`Progresso.registrar(params, F, violacao)` a cada lote avaliado (F no sentido de
minimização, como no `_ProblemaLote`). Guarda:
  - histórico: avaliações, viáveis, melhor valor de cada objetivo (unidades e sentido
    originais) e, com 2+ objetivos, o hipervolume do arquivo (todas as avaliações);
  - a frente atual: viáveis, sem repetidas, não dominadas (1 objetivo: os 20 melhores),
    igual à `Resultado.frente()` / `melhores(20)`.

Hipervolume com normalização FIXA (ideal e nadir dos viáveis da 1ª geração que tiver
algum, referência 1,1): assim ele nunca diminui, e a curva mostra a convergência de
verdade. Até 3 objetivos é exato (pymoo); com 4 ou mais, Monte Carlo com amostras fixas
(continua sem diminuir: o conjunto de amostras dominadas só cresce).
"""
from __future__ import annotations

import numpy as np

from otimiza.otimizacao.pareto import clusters, nao_dominados

REF = 1.1             # ponto de referência na escala normalizada
MAX_FRENTE = 300      # pontos mandados para a janela (amostra espalhada, como no exportar)
N_MONTE_CARLO = 20000
MELHORES_1OBJ = 20


def _chave(p: dict) -> tuple:
    return tuple(sorted((k, str(v)) for k, v in p.items()))


class Progresso:
    def __init__(self, nomes, sentidos, k_clusters: int = 5, semente: int = 0):
        self.nomes, self.sentidos = list(nomes), list(sentidos)
        self.m = len(self.nomes)
        self.sinal = np.array([1.0 if s == "min" else -1.0 for s in self.sentidos])
        self.k = k_clusters
        self.historico: list[dict] = []
        self.parametros: list[dict] = []
        self.F = np.zeros((0, self.m))
        self.geracao = np.zeros(0, dtype=int)
        self._vistos: set[tuple] = set()
        self._arquivo = np.zeros(0, dtype=int)   # índices viáveis, únicos, não dominados
        self._ideal = self._escala = None
        self._mc = None
        self._viaveis = 0
        self._frente_pronta = None   # frente da janela, refeita só quando chega geração nova

    # ------------------------------------------------------------ registro
    def registrar(self, params: list[dict], F, violacao) -> dict:
        F = np.asarray(F, dtype=float).reshape(len(params), self.m)
        v = np.asarray(violacao, dtype=float).reshape(len(params))
        g = len(self.historico)
        i0 = len(self.parametros)
        self.parametros += list(params)
        self.F = np.vstack([self.F, F])
        self.geracao = np.concatenate([self.geracao, np.full(len(params), g)])
        novos = []
        for j, p in enumerate(params):
            if not (v[j] <= 0 and np.isfinite(F[j]).all()):
                continue
            self._viaveis += 1
            k = _chave(p)
            if k in self._vistos:
                continue
            self._vistos.add(k)
            novos.append(i0 + j)
        self._frente_pronta = None
        cand = np.concatenate([self._arquivo, np.array(novos, dtype=int)])
        if self.m == 1:   # 1 objetivo: guarda os melhores (a "frente" seria só o melhor)
            self._arquivo = cand[np.argsort(self.F[cand, 0], kind="stable")[:MELHORES_1OBJ]]
        elif len(cand):
            self._arquivo = cand[nao_dominados(self.F[cand])]
        if self._ideal is None and len(self._arquivo):
            Fv = self.F[np.array(novos, dtype=int)]
            self._ideal = Fv.min(axis=0)
            self._escala = np.where(Fv.max(axis=0) > self._ideal, Fv.max(axis=0) - self._ideal, 1.0)
        melhor = (self.F[self._arquivo].min(axis=0) * self.sinal).tolist() if len(self._arquivo) \
            else [None] * self.m
        h = dict(geracao=g, avaliacoes=len(self.parametros), viaveis=self._viaveis,
                 n_frente=int(len(self._arquivo)), melhor=melhor,
                 hipervolume=self.hipervolume() if self.m > 1 else None)
        self.historico.append(h)
        return h

    # ------------------------------------------------------------ indicadores
    def normalizado(self, F) -> np.ndarray:
        return (np.asarray(F, float) - self._ideal) / self._escala

    def hipervolume(self) -> float:
        if self._ideal is None or not len(self._arquivo):
            return 0.0
        Fn = self.normalizado(self.F[self._arquivo])
        Fn = Fn[(Fn < REF).all(axis=1)]
        if not len(Fn):
            return 0.0
        if self.m <= 3:
            from pymoo.indicators.hv import HV
            return float(HV(ref_point=np.full(self.m, REF))(Fn))
        # Monte Carlo com amostras fixas na caixa [-1, REF]^m
        if self._mc is None:
            self._mc = np.random.default_rng(0).uniform(-1.0, REF, (N_MONTE_CARLO, self.m))
        dom = np.zeros(len(self._mc), dtype=bool)
        for f in Fn:                     # amostra dominada por algum ponto da frente
            dom |= (self._mc >= f).all(axis=1)
        return float(dom.mean() * (REF + 1.0) ** self.m)

    # ------------------------------------------------------------ frente
    def indices_frente(self) -> np.ndarray:
        """Frente em ordem do 1º objetivo (como no `exportar`)."""
        a = self._arquivo
        return a[np.argsort(self.F[a, 0], kind="stable")]

    def frente(self, maximo: int = MAX_FRENTE) -> list[dict]:
        idx = self.indices_frente()
        if len(idx) > maximo:
            idx = idx[np.unique(np.linspace(0, len(idx) - 1, maximo).round().astype(int))]
        rot = clusters(self.F[idx], self.k)[0] if self.m > 1 and len(idx) else np.zeros(len(idx), int)
        obj = self.F[idx] * self.sinal
        return [dict(indice=int(i), geracao=int(self.geracao[i]), cluster=int(rot[j]),
                     objetivos=[float(x) for x in obj[j]],
                     parametros={k: (v if isinstance(v, (int, float)) else str(v))
                                 for k, v in self.parametros[i].items()})
                for j, i in enumerate(idx)]

    def dict(self, desde: int = 0) -> dict:
        """Histórico a partir da geração `desde` + a frente atual."""
        return dict(objetivos=[dict(nome=n, sentido=s) for n, s in zip(self.nomes, self.sentidos)],
                    geracoes_registradas=len(self.historico),
                    historico=self.historico[max(0, int(desde)):],
                    frente=self._frente_cache(), n_frente=int(len(self._arquivo)))

    def _frente_cache(self) -> list[dict]:
        # a janela consulta a cada 0,5 s: clusters e listas só uma vez por geração
        if self._frente_pronta is None:
            self._frente_pronta = self.frente()
        return self._frente_pronta

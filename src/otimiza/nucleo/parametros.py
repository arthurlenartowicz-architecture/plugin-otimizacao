"""Parâmetros de projeto (contínuos, inteiros, categóricos) e montagem da cena em lote.

O otimizador trabalha num vetor u ∈ [0, 1]^d (um número por parâmetro).
Cada parâmetro sabe traduzir u para o seu valor:
  contínuo   → mínimo + u·(máximo − mínimo)
  inteiro    → faixas iguais de u, uma por valor (mínimo … máximo)
  categórico → faixas iguais de u, uma por opção
Assim os mesmos operadores (SBX + mutação polinomial) servem a todos os tipos.
`arredondar` leva os parâmetros discretos para o centro da sua faixa: dois
vetores com os mesmos valores ficam iguais (o otimizador elimina duplicatas).

Um gerador é uma função  dict de parâmetros → Variante (sol_luz) ou
Estrutura (estrutura); `cena_de` chama o gerador para a população inteira e
monta a CenaLote.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass
from typing import Any, Callable, Sequence

import numpy as np


@dataclass(frozen=True)
class Continuo:
    nome: str
    minimo: float
    maximo: float

    def __post_init__(self):
        if not self.maximo > self.minimo:
            raise ValueError(f"{self.nome}: máximo deve ser maior que o mínimo")

    def decodificar(self, u: np.ndarray) -> list:
        return list(self.minimo + np.clip(u, 0, 1) * (self.maximo - self.minimo))

    def codificar(self, valor) -> float:
        return float(np.clip((valor - self.minimo) / (self.maximo - self.minimo), 0, 1))

    def valores(self, n: int) -> list:
        return list(np.linspace(self.minimo, self.maximo, n))


class _Discreto:
    def opcoes_(self) -> list:
        raise NotImplementedError

    def _indice(self, u: np.ndarray) -> np.ndarray:
        k = len(self.opcoes_())
        return np.clip(np.floor(np.clip(u, 0, 1) * k), 0, k - 1).astype(int)

    def decodificar(self, u: np.ndarray) -> list:
        ops = self.opcoes_()
        return [ops[i] for i in self._indice(u)]

    def codificar(self, valor) -> float:
        ops = self.opcoes_()
        return (ops.index(valor) + 0.5) / len(ops)

    def centro(self, u: np.ndarray) -> np.ndarray:
        return (self._indice(u) + 0.5) / len(self.opcoes_())

    def valores(self, n: int = 0) -> list:
        return list(self.opcoes_())


@dataclass(frozen=True)
class Inteiro(_Discreto):
    nome: str
    minimo: int
    maximo: int

    def __post_init__(self):
        if self.maximo < self.minimo:
            raise ValueError(f"{self.nome}: máximo deve ser ≥ mínimo")

    def opcoes_(self) -> list:
        return list(range(int(self.minimo), int(self.maximo) + 1))


@dataclass(frozen=True)
class Categorico(_Discreto):
    nome: str
    opcoes: tuple

    def __post_init__(self):
        if len(self.opcoes) < 1:
            raise ValueError(f"{self.nome}: categórico sem opções")
        object.__setattr__(self, "opcoes", tuple(self.opcoes))

    def opcoes_(self) -> list:
        return list(self.opcoes)


class Espaco:
    """Conjunto ordenado de parâmetros."""

    def __init__(self, parametros: Sequence):
        self.parametros = list(parametros)
        nomes = [p.nome for p in self.parametros]
        if len(set(nomes)) != len(nomes):
            raise ValueError("nomes de parâmetros repetidos")
        self.nomes = nomes

    @property
    def n(self) -> int:
        return len(self.parametros)

    def decodificar(self, U) -> list[dict]:
        U = np.atleast_2d(np.asarray(U, dtype=float))
        colunas = [p.decodificar(U[:, j]) for j, p in enumerate(self.parametros)]
        return [{nome: _nativo(col[i]) for nome, col in zip(self.nomes, colunas)}
                for i in range(U.shape[0])]

    def codificar(self, valores: dict | Sequence[dict]) -> np.ndarray:
        lista = [valores] if isinstance(valores, dict) else list(valores)
        return np.array([[p.codificar(v[p.nome]) for p in self.parametros] for v in lista])

    def arredondar(self, U: np.ndarray) -> np.ndarray:
        """Discretos no centro da faixa; contínuos só limitados a [0, 1]."""
        U = np.clip(np.array(U, dtype=float), 0, 1)
        for j, p in enumerate(self.parametros):
            if isinstance(p, _Discreto):
                U[:, j] = p.centro(U[:, j])
        return U

    def grade(self, n_continuo: int = 5) -> list[dict]:
        """Todas as combinações (contínuos com n valores igualmente espaçados)."""
        listas = [p.valores(n_continuo) for p in self.parametros]
        return [{n: _nativo(v) for n, v in zip(self.nomes, combo)}
                for combo in itertools.product(*listas)]

    def descrever(self) -> list[dict]:
        saida = []
        for p in self.parametros:
            if isinstance(p, Continuo):
                saida.append(dict(nome=p.nome, tipo="continuo", minimo=p.minimo, maximo=p.maximo))
            elif isinstance(p, Inteiro):
                saida.append(dict(nome=p.nome, tipo="inteiro", minimo=p.minimo, maximo=p.maximo))
            else:
                saida.append(dict(nome=p.nome, tipo="categorico", opcoes=[str(o) for o in p.opcoes]))
        return saida


def _nativo(v: Any) -> Any:
    """numpy → tipos do Python (para JSON e para os geradores)."""
    if isinstance(v, np.integer):
        return int(v)
    if isinstance(v, np.floating):
        return float(v)
    return v


def cena_de(gerador: Callable[[dict], Any], parametros: Sequence[dict], clima=None, device=None):
    """CenaLote com uma variante por dict de parâmetros."""
    from otimiza.modulos.estrutura.fem import Estrutura
    from otimiza.nucleo.cena import CenaLote, Variante

    objetos = [gerador(p) for p in parametros]
    extra = {} if device is None else {"device": device}
    if all(isinstance(o, Estrutura) for o in objetos):
        return CenaLote(variantes=[], clima=clima, estruturas=objetos, **extra)
    if all(isinstance(o, Variante) for o in objetos):
        return CenaLote(variantes=objetos, clima=clima, **extra)
    raise TypeError("o gerador deve devolver só Variante ou só Estrutura")

"""Contrato comum dos módulos.

Todo módulo declara:
  nome        — ex.: "sol_luz"
  depende_de  — nomes de outros módulos cujas métricas ele usa
  metricas    — {nome: Metrica(unidade, sentido, descrição)}
e implementa
  avaliar(cena, cache, metricas, nivel) -> {nome_metrica: Tensor (V,)}
com um valor por variante. Só as métricas pedidas precisam ser calculadas.
Métricas de outros módulos chegam em `cache.resultados["modulo.metrica"]`; `precisa` diz
quais (o padrão é todas as das dependências).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

import torch

NIVEIS = ("rapido", "padrao", "validacao")


@dataclass(frozen=True)
class Metrica:
    unidade: str
    sentido: str          # "min" ou "max" (padrão sugerido; o problema pode trocar)
    descricao: str = ""

    def __post_init__(self):
        if self.sentido not in ("min", "max"):
            raise ValueError(f"sentido deve ser 'min' ou 'max', não {self.sentido!r}")


class Modulo(ABC):
    nome: str = ""
    depende_de: tuple[str, ...] = ()
    metricas: dict[str, Metrica] = {}

    @abstractmethod
    def avaliar(self, cena, cache, metricas: set[str], nivel: str = "padrao") -> dict[str, torch.Tensor]:
        ...

    def precisa(self, metricas: set[str]) -> dict[str, set[str]] | None:
        """Métricas das dependências que as `metricas` pedidas usam ({módulo: {métricas}}).
        None = todas as métricas de cada módulo em `depende_de` (o padrão)."""
        return None


_REGISTRO: dict[str, Modulo] = {}


def registrar(modulo: Modulo) -> Modulo:
    if not modulo.nome:
        raise ValueError("módulo sem nome")
    _REGISTRO[modulo.nome] = modulo
    return modulo


def modulo(nome: str) -> Modulo:
    if nome not in _REGISTRO:
        raise KeyError(f"módulo {nome!r} não registrado (registrados: {sorted(_REGISTRO)})")
    return _REGISTRO[nome]


def registrados() -> dict[str, Modulo]:
    return dict(_REGISTRO)

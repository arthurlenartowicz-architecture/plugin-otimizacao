"""Cache de cálculos caros e reaproveitáveis entre variantes e entre avaliações.

Ex.: posição do sol e matrizes de céu dependem só do clima; ficam guardadas e
servem a todas as variantes de todas as gerações do otimizador.
"""
from __future__ import annotations

import hashlib
from typing import Any, Callable

import torch


class Cache:
    def __init__(self):
        self._dados: dict[tuple, Any] = {}
        self.resultados: dict[str, torch.Tensor] = {}  # "modulo.metrica" -> (V,)
        self.acertos = 0
        self.faltas = 0

    def obter(self, chave: tuple, calcular: Callable[[], Any]) -> Any:
        if chave in self._dados:
            self.acertos += 1
            return self._dados[chave]
        self.faltas += 1
        valor = calcular()
        self._dados[chave] = valor
        return valor

    def limpar(self):
        self._dados.clear()
        self.resultados.clear()


def impressao(*tensores: torch.Tensor) -> str:
    """Identificador curto do conteúdo de tensores (para chaves de cache)."""
    h = hashlib.sha1()
    for t in tensores:
        h.update(t.detach().cpu().contiguous().numpy().tobytes())
        h.update(str(tuple(t.shape)).encode())
    return h.hexdigest()[:16]

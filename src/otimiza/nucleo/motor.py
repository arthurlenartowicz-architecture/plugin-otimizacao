"""Motor: avalia um problema (lista de "modulo.metrica") rodando só o necessário."""
from __future__ import annotations

from dataclasses import dataclass, field

import torch

from otimiza.nucleo.cache import Cache
from otimiza.nucleo.contrato import NIVEIS, modulo


@dataclass
class Problema:
    objetivos: list[str]                                   # ["sol_luz.sda", ...]
    sentidos: dict[str, str] = field(default_factory=dict)  # troca o sentido padrão
    restricoes: list[tuple[str, str, float]] = field(default_factory=list)  # ("x.y", ">=", 50)

    def metricas(self) -> list[str]:
        return list(dict.fromkeys(self.objetivos + [r[0] for r in self.restricoes]))

    def sentido(self, chave: str) -> str:
        if chave in self.sentidos:
            return self.sentidos[chave]
        nome_mod, nome_met = separar(chave)
        return modulo(nome_mod).metricas[nome_met].sentido


def separar(chave: str) -> tuple[str, str]:
    if chave.count(".") != 1:
        raise ValueError(f"use o formato 'modulo.metrica', não {chave!r}")
    return tuple(chave.split("."))  # type: ignore[return-value]


def ordem_de_execucao(nomes_modulos: set[str]) -> list[str]:
    """Inclui dependências e ordena (dependências antes)."""
    ordem, visitando, feito = [], set(), set()

    def visitar(n):
        if n in feito:
            return
        if n in visitando:
            raise ValueError(f"dependência circular envolvendo {n!r}")
        visitando.add(n)
        for d in modulo(n).depende_de:
            visitar(d)
        visitando.discard(n)
        feito.add(n)
        ordem.append(n)

    for n in sorted(nomes_modulos):
        visitar(n)
    return ordem


def avaliar(cena, chaves: list[str], nivel: str = "padrao", cache: Cache | None = None
            ) -> dict[str, torch.Tensor]:
    """{"modulo.metrica": Tensor (V,)} para as chaves pedidas."""
    if nivel not in NIVEIS:
        raise ValueError(f"nível deve ser um de {NIVEIS}")
    cache = cache or Cache()
    pedidas: dict[str, set[str]] = {}
    for c in chaves:
        m, met = separar(c)
        if met not in modulo(m).metricas:
            raise KeyError(f"{m!r} não tem a métrica {met!r} (tem: {sorted(modulo(m).metricas)})")
        pedidas.setdefault(m, set()).add(met)
    # métricas das dependências: as que o módulo declara usar (precisa) ou todas (padrão)
    fila = list(pedidas)
    while fila:
        nome = fila.pop()
        req = modulo(nome).precisa(set(pedidas[nome]))
        if req is None:
            req = {d: set(modulo(d).metricas) for d in modulo(nome).depende_de}
        for dep, mets in req.items():
            if not mets <= pedidas.get(dep, set()):
                pedidas.setdefault(dep, set()).update(mets)
                fila.append(dep)
    for nome in ordem_de_execucao(set(pedidas)):
        if nome not in pedidas:   # dependência de que nenhuma métrica pedida precisa
            continue
        mets = pedidas[nome]
        saida = modulo(nome).avaliar(cena, cache, mets, nivel)
        for met, valor in saida.items():
            if valor.shape != (cena.n,):
                raise ValueError(f"{nome}.{met} devolveu forma {tuple(valor.shape)}, esperado ({cena.n},)")
            cache.resultados[f"{nome}.{met}"] = valor
    return {c: cache.resultados[c] for c in chaves}


def avaliar_problema(cena, problema: Problema, nivel: str = "padrao", cache: Cache | None = None):
    """Objetivos já no sentido de minimização (max vira −valor) e violação das restrições.

    Retorna (F (V, n_obj), violacao (V,)) — violação 0 = viável.
    """
    valores = avaliar(cena, problema.metricas(), nivel, cache)
    f = torch.stack([valores[o] if problema.sentido(o) == "min" else -valores[o]
                     for o in problema.objetivos], dim=1)
    viol = torch.zeros(cena.n, dtype=f.dtype, device=f.device)
    for chave, op, lim in problema.restricoes:
        v = valores[chave].to(f)
        if op == ">=":
            viol = viol + (lim - v).clamp(min=0)
        elif op == "<=":
            viol = viol + (v - lim).clamp(min=0)
        else:
            raise ValueError(f"operador de restrição deve ser '>=' ou '<=', não {op!r}")
    return f, viol

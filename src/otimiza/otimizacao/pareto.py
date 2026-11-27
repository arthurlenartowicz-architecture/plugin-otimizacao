"""Frente de Pareto: não dominadas, indicadores, clusters, coordenadas paralelas e exportação.

Convenção: F sempre no sentido de minimização (como sai do otimizador).
"""
from __future__ import annotations

import json
import os

import numpy as np


def nao_dominados(F: np.ndarray) -> np.ndarray:
    """Índices das linhas de F que nenhuma outra domina (≤ em tudo e < em algum)."""
    from pymoo.util.nds.non_dominated_sorting import NonDominatedSorting
    F = np.asarray(F, dtype=float)
    if len(F) == 0:
        return np.array([], dtype=int)
    return np.sort(NonDominatedSorting().do(F, only_non_dominated_front=True))


def igd(F: np.ndarray, frente_ref: np.ndarray) -> float:
    """Inverted Generational Distance: distância média de cada ponto da frente de
    referência ao ponto mais próximo de F (0 = cobre a frente toda)."""
    from pymoo.indicators.igd import IGD
    return float(IGD(np.asarray(frente_ref, float))(np.asarray(F, float)))


def hipervolume(F: np.ndarray, ref: np.ndarray) -> float:
    """Volume dominado por F até o ponto de referência `ref` (maior = melhor)."""
    from pymoo.indicators.hv import HV
    F = np.asarray(F, float)
    F = F[(F < np.asarray(ref)).all(axis=1)]
    return float(HV(ref_point=np.asarray(ref, float))(F)) if len(F) else 0.0


def normalizar(F: np.ndarray) -> np.ndarray:
    F = np.asarray(F, float)
    lo, hi = F.min(axis=0), F.max(axis=0)
    return (F - lo) / np.where(hi > lo, hi - lo, 1.0)


def clusters(F: np.ndarray, k: int = 5, semente: int = 0) -> tuple[np.ndarray, np.ndarray]:
    """k-means nos objetivos normalizados.

    Retorna (rótulo de cada ponto, índice do representante de cada cluster =
    ponto mais perto do centro). Clusters numerados pelo 1º objetivo crescente.
    """
    from scipy.cluster.vq import kmeans2
    Fn = normalizar(F)
    n = len(Fn)
    k = max(1, min(k, n))
    if k == 1:
        rot = np.zeros(n, dtype=int)
        centros = Fn.mean(axis=0, keepdims=True)
    else:
        centros, rot = kmeans2(Fn, k, minit="++", seed=semente)
    usados = [c for c in range(len(centros)) if (rot == c).any()]
    ordem = sorted(usados, key=lambda c: centros[c][0])
    novo = {c: i for i, c in enumerate(ordem)}
    rot = np.array([novo[c] for c in rot])
    reps = []
    for i, c in enumerate(ordem):
        membros = np.flatnonzero(rot == i)
        d = np.linalg.norm(Fn[membros] - centros[c], axis=1)
        reps.append(membros[np.argmin(d)])
    return rot, np.array(reps)


def coordenadas_paralelas(colunas: dict[str, np.ndarray], arquivo: str, rotulos=None,
                          destaque=None, titulo: str = "") -> str:
    """Gráfico de coordenadas paralelas (PNG). Cada eixo vai do menor ao maior valor
    da coluna; `rotulos` colore as linhas (clusters); `destaque` = índices em negrito."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    nomes = list(colunas)
    M = np.column_stack([np.asarray(colunas[n], dtype=float) for n in nomes])
    lo, hi = M.min(axis=0), M.max(axis=0)
    Mn = (M - lo) / np.where(hi > lo, hi - lo, 1.0)
    x = np.arange(len(nomes))
    fig, ax = plt.subplots(figsize=(max(6, 1.6 * len(nomes)), 4.5))
    cores = plt.get_cmap("tab10")
    for i in range(len(M)):
        cor = cores(int(rotulos[i]) % 10) if rotulos is not None else "0.4"
        ax.plot(x, Mn[i], color=cor, alpha=0.35, lw=1)
    for i in (destaque if destaque is not None else []):
        cor = cores(int(rotulos[i]) % 10) if rotulos is not None else "k"
        ax.plot(x, Mn[i], color=cor, lw=2.5)
    for j in x:
        ax.axvline(j, color="k", lw=0.8)
        ax.text(j, 1.03, f"{hi[j]:.3g}", ha="center", va="bottom", fontsize=8)
        ax.text(j, -0.03, f"{lo[j]:.3g}", ha="center", va="top", fontsize=8)
    ax.set_xticks(x, nomes, rotation=15)
    ax.set_yticks([])
    ax.set_ylim(-0.12, 1.12)
    for lado in ("top", "right", "left"):
        ax.spines[lado].set_visible(False)
    if titulo:
        ax.set_title(titulo, pad=18)
    fig.tight_layout()
    os.makedirs(os.path.dirname(os.path.abspath(arquivo)), exist_ok=True)
    fig.savefig(arquivo, dpi=130)
    plt.close(fig)
    return arquivo


def _geometria(obj) -> dict:
    """Malha (Variante) ou barras (Estrutura) em listas, para o Grasshopper."""
    from otimiza.modulos.estrutura.fem import Estrutura
    if isinstance(obj, Estrutura):
        return dict(tipo="barras", nos=obj.nos.double().cpu().numpy().round(6).tolist(),
                    barras=obj.barras.cpu().numpy().astype(int).tolist())
    return dict(tipo="malha", vertices=obj.vertices.double().cpu().numpy().round(6).tolist(),
                faces=obj.faces.cpu().numpy().astype(int).tolist(),
                rotulos=obj.rotulos.cpu().numpy().astype(int).tolist())


def exportar(resultado, pasta: str, gerador=None, k_clusters: int = 5,
             parametros_no_grafico: bool = True, max_solucoes: int = 200) -> dict:
    """Grava `resultado.json` (frente de Pareto: parâmetros, objetivos, cluster e,
    com `gerador`, a geometria de cada solução) e `paralelas.png` na pasta.

    Soluções em ordem do 1º objetivo. Frente com mais de `max_solucoes` pontos: fica
    uma amostra espalhada (igualmente espaçada nessa ordem), para o arquivo não crescer."""
    os.makedirs(pasta, exist_ok=True)
    # 1 objetivo: a "frente" é só o melhor; exporta os 20 melhores distintos para comparar
    idx = resultado.frente() if len(resultado.nomes) > 1 else resultado.melhores(20)
    idx = idx[np.argsort(resultado.F[idx, 0], kind="stable")]   # slider percorre a frente
    if len(idx) > max_solucoes:
        idx = idx[np.unique(np.linspace(0, len(idx) - 1, max_solucoes).round().astype(int))]
    obj = resultado.objetivos[idx]
    rot, reps = clusters(resultado.F[idx], k_clusters) if len(idx) else (np.array([]), [])
    solucoes = []
    for j, i in enumerate(idx):
        s = dict(indice=int(i), geracao=int(resultado.geracao[i]), cluster=int(rot[j]),
                 representante=bool(j in set(reps)),
                 parametros={k: (v if isinstance(v, (int, float)) else str(v))
                             for k, v in resultado.parametros[i].items()},
                 objetivos={n: float(v) for n, v in zip(resultado.nomes, obj[j])})
        if gerador is not None:
            s["geometria"] = _geometria(gerador(resultado.parametros[i]))
        solucoes.append(s)
    from otimiza.nucleo.cena import ROTULOS
    dados = dict(algoritmo=resultado.algoritmo, rotulos=list(ROTULOS), tempo_s=round(resultado.tempo, 3),
                 info=resultado.info, parametros=resultado.espaco.descrever(),
                 objetivos=[dict(nome=n, sentido=s) for n, s in
                            zip(resultado.nomes, resultado.sentidos)],
                 n_avaliacoes=len(resultado.parametros),
                 n_viaveis=int(resultado.viavel.sum()), solucoes=solucoes)
    with open(os.path.join(pasta, "resultado.json"), "w", encoding="utf-8") as f:
        json.dump(dados, f, ensure_ascii=False, indent=1)
    if len(idx) > 1:
        colunas = {n: obj[:, j] for j, n in enumerate(resultado.nomes)}
        if parametros_no_grafico:
            for p in resultado.espaco.parametros:
                vals = [resultado.parametros[i][p.nome] for i in idx]
                if not all(isinstance(v, (int, float)) for v in vals):
                    ops = [str(o) for o in p.opcoes_()]
                    vals = [ops.index(str(v)) for v in vals]
                colunas[p.nome] = np.array(vals, float)
        coordenadas_paralelas(colunas, os.path.join(pasta, "paralelas.png"), rot, reps,
                              f"Pareto: {len(idx)} soluções, {len(reps)} clusters")
    return dados

"""Geometria: malhas trianguladas e malhas de sensores.

Uma malha é um par de tensores:
  vertices (V, 3) float, em metros, eixos do Rhino (x leste, y norte, z cima)
  faces    (F, 3) int64, índices dos vértices de cada triângulo
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import torch


def ler_malha_json(caminho: str | Path, device=None) -> tuple[torch.Tensor, torch.Tensor]:
    """Lê o formato gravado pelo exportador do Grasshopper ({"vertices", "faces"})."""
    with open(caminho, encoding="utf-8") as f:
        d = json.load(f)
    v = torch.tensor(d["vertices"], dtype=torch.float64, device=device)
    fc = torch.tensor(d["faces"], dtype=torch.int64, device=device)
    return v, fc


def normais_e_areas(vertices: torch.Tensor, faces: torch.Tensor):
    """(F, 3) normal unitária e (F,) área de cada triângulo (regra da mão direita)."""
    a, b, c = (vertices[faces[:, i]] for i in range(3))
    cruz = torch.linalg.cross(b - a, c - a)
    dobro_area = cruz.norm(dim=1)
    return cruz / dobro_area.clamp(min=1e-30)[:, None], dobro_area / 2


def distancia_pontos_triangulos(p: torch.Tensor, vertices: torch.Tensor, faces: torch.Tensor):
    """(N, T) distância de cada ponto a cada triângulo (ponto mais próximo)."""
    a, b, c = (vertices[faces[:, i]][None] for i in range(3))  # (1, T, 3)
    q = p[:, None, :]                                           # (N, 1, 3)
    n, _ = normais_e_areas(vertices, faces)
    n = n[None]
    d_plano = ((q - a) * n).sum(-1)                             # (N, T)
    proj = q - d_plano[..., None] * n
    # dentro do triângulo se as três arestas "veem" a projeção do mesmo lado
    def lado(u, v):
        return (torch.linalg.cross(v - u, proj - u) * n).sum(-1)
    dentro = (lado(a, b) >= 0) & (lado(b, c) >= 0) & (lado(c, a) >= 0)

    def seg(u, v):
        uv = v - u
        t = (((q - u) * uv).sum(-1) / (uv * uv).sum(-1).clamp(min=1e-30)).clamp(0, 1)
        return (q - (u + t[..., None] * uv)).norm(dim=-1)
    d_borda = torch.minimum(torch.minimum(seg(a, b), seg(b, c)), seg(c, a))
    return torch.where(dentro, d_plano.abs(), d_borda)


def sensores_encostados(centros, normais, vertices, faces, tol: float = 0.05) -> torch.Tensor:
    """(S,) bool: sensor cujo centro toca uma superfície não paralela à dele
    (ex.: ponto do piso exatamente na base de uma parede). Resultado ambíguo
    para qualquer motor de raios; útil para excluir de comparações."""
    nf, _ = normais_e_areas(vertices, faces)
    d = distancia_pontos_triangulos(centros, vertices, faces)
    paralela = (normais @ nf.T).abs() > 0.99
    return ((d < tol) & ~paralela).any(dim=1)


@dataclass
class Sensores:
    pontos: torch.Tensor   # (S, 3) já deslocados ao longo da normal
    normais: torch.Tensor  # (S, 3) unitárias
    areas: torch.Tensor    # (S,) área representada por cada sensor, m²
    face: torch.Tensor     # (S,) triângulo de origem

    @property
    def n(self) -> int:
        return int(self.pontos.shape[0])


def sensores_de_malha(
    vertices: torch.Tensor, faces: torch.Tensor, espacamento: float, deslocamento: float = 0.0
) -> Sensores:
    """Sensores distribuídos sobre a malha, ~1 a cada `espacamento` metros.

    Cada triângulo é subdividido em n² triângulos iguais e um sensor vai no
    centro de cada um. n é escolhido para que cada sensor represente ~espaçamento²
    de área (n = √área / espaçamento), sem deixar sub-triângulos compridos demais
    em triângulos finos (n ≥ maior aresta / (3·espaçamento)).
    Assim todo sensor representa a mesma fração da área do seu triângulo, e
    somar valor × área dá o total na superfície.
    """
    if espacamento <= 0:
        raise ValueError("espacamento precisa ser > 0")
    normal_f, area_f = normais_e_areas(vertices, faces)
    a, b, c = (vertices[faces[:, i]] for i in range(3))
    aresta = torch.stack([(b - a).norm(dim=1), (c - b).norm(dim=1), (a - c).norm(dim=1)], 1).amax(1)
    # folga relativa de 1e-6: 6,0000001 (ruído de float, ex.: geometria do GH em mm) dá 6, não 7
    n_div = torch.ceil(torch.maximum(area_f.sqrt() / espacamento,
                                     aresta / (3 * espacamento)) * (1 - 1e-6)).clamp(min=1).long()

    pontos, face_idx, area = [], [], []
    # agrupa triângulos com o mesmo n para fazer tudo em lote
    for n in torch.unique(n_div).tolist():
        sel = (n_div == n).nonzero(as_tuple=True)[0]
        bar = _baricentros_centros(n, vertices.dtype, vertices.device)  # (n², 3)
        p = (bar[None, :, 0:1] * a[sel, None] + bar[None, :, 1:2] * b[sel, None]
             + bar[None, :, 2:3] * c[sel, None])                         # (k, n², 3)
        pontos.append(p.reshape(-1, 3))
        face_idx.append(sel.repeat_interleave(n * n))
        area.append((area_f[sel] / (n * n)).repeat_interleave(n * n))

    face_idx = torch.cat(face_idx)
    normais = normal_f[face_idx]
    pontos = torch.cat(pontos) + deslocamento * normais
    return Sensores(pontos, normais, torch.cat(area), face_idx)


def _baricentros_centros(n: int, dtype, device) -> torch.Tensor:
    """Coordenadas baricêntricas dos centros dos n² sub-triângulos."""
    lista = []
    for i in range(n):
        for j in range(n - i):
            # triângulo "para cima"
            lista.append(((i + 1 / 3) / n, (j + 1 / 3) / n))
            # triângulo "para baixo" (existe quando i + j < n - 1)
            if i + j < n - 1:
                lista.append(((i + 2 / 3) / n, (j + 2 / 3) / n))
    uv = torch.tensor(lista, dtype=dtype, device=device)
    return torch.stack([1 - uv[:, 0] - uv[:, 1], uv[:, 0], uv[:, 1]], dim=1)


def caixa(x0, y0, z0, x1, y1, z1) -> tuple[torch.Tensor, torch.Tensor]:
    """Malha de uma caixa alinhada aos eixos, normais para fora (útil em testes)."""
    v = torch.tensor(
        [[x0, y0, z0], [x1, y0, z0], [x1, y1, z0], [x0, y1, z0],
         [x0, y0, z1], [x1, y0, z1], [x1, y1, z1], [x0, y1, z1]], dtype=torch.float64)
    f = torch.tensor(
        [[0, 2, 1], [0, 3, 2], [4, 5, 6], [4, 6, 7], [0, 1, 5], [0, 5, 4],
         [1, 2, 6], [1, 6, 5], [2, 3, 7], [2, 7, 6], [3, 0, 4], [3, 4, 7]], dtype=torch.int64)
    return v, f


def retangulo(x0, y0, x1, y1, z=0.0) -> tuple[torch.Tensor, torch.Tensor]:
    """Retângulo horizontal com normal para cima."""
    v = torch.tensor([[x0, y0, z], [x1, y0, z], [x1, y1, z], [x0, y1, z]], dtype=torch.float64)
    f = torch.tensor([[0, 1, 2], [0, 2, 3]], dtype=torch.int64)
    return v, f


def juntar(*malhas) -> tuple[torch.Tensor, torch.Tensor]:
    vs, fs, base = [], [], 0
    for v, f in malhas:
        vs.append(v)
        fs.append(f + base)
        base += v.shape[0]
    return torch.cat(vs), torch.cat(fs)

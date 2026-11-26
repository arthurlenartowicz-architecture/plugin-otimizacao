"""Raycasting em lote na GPU com NVIDIA Warp.

`Cena` guarda uma malha por variante (cada uma com sua BVH, construída pelo
Warp ao criar o `wp.Mesh`). Cada raio leva o índice da sua variante, então
milhares de variantes são testadas num único lançamento de kernel.

Geometria na GPU em float32 (é o que o Warp usa); por isso os pontos de origem
precisam de um pequeno deslocamento da superfície (ver `EPS_ORIGEM`).
"""
from __future__ import annotations

import torch
import warp as wp

EPS_ORIGEM = wp.constant(1e-4)  # m, afasta a origem do raio da própria superfície
T_MAX = 1e6        # m, "infinito"

wp.config.quiet = True
wp.init()


def dispositivo_padrao() -> str:
    return "cuda:0" if wp.is_cuda_available() else "cpu"


def _vec3(t: torch.Tensor, device: str) -> wp.array:
    t = t.to(device=device, dtype=torch.float32).contiguous()
    return wp.from_torch(t, dtype=wp.vec3)


class Cena:
    """Uma malha (com BVH) por variante."""

    def __init__(self, malhas: list[tuple[torch.Tensor, torch.Tensor]], device: str | None = None):
        self.device = device or dispositivo_padrao()
        self._malhas = []  # mantém vivos os wp.Mesh e seus arrays
        for v, f in malhas:
            pts = _vec3(v, self.device)
            idx = wp.from_torch(f.to(device=self.device, dtype=torch.int32).reshape(-1).contiguous())
            self._malhas.append(wp.Mesh(points=pts, indices=idx))
        self.ids = wp.array([m.id for m in self._malhas], dtype=wp.uint64, device=self.device)

    @property
    def n_variantes(self) -> int:
        return len(self._malhas)

    def atualizar(self, variante: int, vertices: torch.Tensor):
        """Move os vértices de uma variante (mesma topologia) e reajusta a BVH."""
        m = self._malhas[variante]
        wp.copy(m.points, _vec3(vertices, self.device))
        m.refit()

    # ------------------------------------------------------------------ consultas
    def visivel(self, origens, direcoes, variante=None, t_max: float = T_MAX) -> torch.Tensor:
        """(N,) bool: o raio i (origem i, direção i) NÃO bate em nada."""
        n = origens.shape[0]
        var = self._variantes(variante, n)
        saida = torch.empty(n, dtype=torch.uint8, device=self.device)
        wp.launch(_k_visivel_pares, dim=n, device=self.device, inputs=[
            self.ids, var, _vec3(origens, self.device), _vec3(direcoes, self.device),
            float(t_max), wp.from_torch(saida)])
        return saida.bool()

    def visibilidade(self, origens, direcoes, variante=None, normais=None,
                     t_max: float = T_MAX) -> torch.Tensor:
        """(S, D) bool: do ponto s se vê a direção d sem obstrução.

        Todas as combinações ponto × direção (ex.: sensores × patches do céu ou
        sensores × posições do sol). Se `normais` for dado, direções atrás da
        superfície (n·d <= 0) contam como não visíveis.
        """
        s, d = origens.shape[0], direcoes.shape[0]
        var = self._variantes(variante, s)
        saida = torch.empty(s, d, dtype=torch.uint8, device=self.device)
        wp.launch(_k_visibilidade, dim=(s, d), device=self.device, inputs=[
            self.ids, var, _vec3(origens, self.device), _vec3(direcoes, self.device),
            float(t_max), wp.from_torch(saida)])
        vis = saida.bool()
        if normais is not None:
            n = normais.to(device=self.device, dtype=torch.float32)
            dd = direcoes.to(device=self.device, dtype=torch.float32)
            vis &= (n @ dd.T) > 0
        return vis

    def intersectar(self, origens, direcoes, variante=None, t_max: float = T_MAX):
        """Primeiro impacto de cada raio: (t, face). t = inf e face = -1 se não bater."""
        n = origens.shape[0]
        var = self._variantes(variante, n)
        t = torch.empty(n, dtype=torch.float32, device=self.device)
        face = torch.empty(n, dtype=torch.int32, device=self.device)
        wp.launch(_k_intersectar, dim=n, device=self.device, inputs=[
            self.ids, var, _vec3(origens, self.device), _vec3(direcoes, self.device),
            float(t_max), wp.from_torch(t), wp.from_torch(face)])
        return t, face

    def _variantes(self, variante, n) -> wp.array:
        if variante is None:
            if self.n_variantes != 1:
                raise ValueError("cena com várias variantes: informe `variante` para cada ponto")
            v = torch.zeros(n, dtype=torch.int32, device=self.device)
        elif isinstance(variante, int):
            v = torch.full((n,), variante, dtype=torch.int32, device=self.device)
        else:
            v = variante.to(device=self.device, dtype=torch.int32).contiguous()
        return wp.from_torch(v)


# ---------------------------------------------------------------------- kernels
@wp.kernel
def _k_visivel_pares(ids: wp.array(dtype=wp.uint64), var: wp.array(dtype=wp.int32),
                     orig: wp.array(dtype=wp.vec3), dirs: wp.array(dtype=wp.vec3),
                     t_max: float, saida: wp.array(dtype=wp.uint8)):
    i = wp.tid()
    d = wp.normalize(dirs[i])
    q = wp.mesh_query_ray(ids[var[i]], orig[i] + d * EPS_ORIGEM, d, t_max)
    saida[i] = wp.where(q.result, wp.uint8(0), wp.uint8(1))


@wp.kernel
def _k_visibilidade(ids: wp.array(dtype=wp.uint64), var: wp.array(dtype=wp.int32),
                    orig: wp.array(dtype=wp.vec3), dirs: wp.array(dtype=wp.vec3),
                    t_max: float, saida: wp.array2d(dtype=wp.uint8)):
    i, j = wp.tid()
    d = wp.normalize(dirs[j])
    q = wp.mesh_query_ray(ids[var[i]], orig[i] + d * EPS_ORIGEM, d, t_max)
    saida[i, j] = wp.where(q.result, wp.uint8(0), wp.uint8(1))


@wp.kernel
def _k_intersectar(ids: wp.array(dtype=wp.uint64), var: wp.array(dtype=wp.int32),
                   orig: wp.array(dtype=wp.vec3), dirs: wp.array(dtype=wp.vec3),
                   t_max: float, t_out: wp.array(dtype=float), face_out: wp.array(dtype=wp.int32)):
    i = wp.tid()
    d = wp.normalize(dirs[i])
    q = wp.mesh_query_ray(ids[var[i]], orig[i] + d * EPS_ORIGEM, d, t_max)
    if q.result:
        t_out[i] = q.t + EPS_ORIGEM
        face_out[i] = q.face
    else:
        t_out[i] = wp.inf
        face_out[i] = -1

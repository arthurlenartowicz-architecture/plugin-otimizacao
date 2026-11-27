"""FEM de barras (treliças e pórticos 3D) em lote na GPU.

Unidades: kN, m (tensões e módulos em kN/m², momentos em kN·m, massa em kg).
Elemento de pórtico de Euler-Bernoulli (12 graus de liberdade: ux uy uz rx ry rz
em cada nó) ou de treliça (só axial). Cada variante é uma `Estrutura`; as
variantes são completadas até o mesmo número de graus de liberdade e resolvidas
juntas com Cholesky denso em float64 (`torch.linalg`).

Eixos locais (padrão do Karamba, a conferir na validação): x ao longo da barra
(nó i → nó j); y = Z × x (horizontal); z = x × y ("para cima"). Barras
verticais: z = X global, y = z × x.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import torch

DT = torch.float64
ENGASTE = (True,) * 6
ARTICULADO = (True, True, True, False, False, False)
PONTOS_ESFORCO = (0.0, 0.25, 0.5, 0.75, 1.0)  # onde a utilização é avaliada em cada elemento


# ---------------------------------------------------------------- seção e material
@dataclass(frozen=True)
class Material:
    E: float     # kN/m²
    G: float     # kN/m²
    rho: float   # kg/m³
    fy: float    # kN/m²


ACO_S235 = Material(E=2.1e8, G=8.076e7, rho=7850.0, fy=2.35e5)  # valores do Karamba


@dataclass(frozen=True)
class Secao:
    A: float     # m²
    Iy: float    # m⁴ (flexão em torno de y local: deslocamento em z)
    Iz: float    # m⁴
    J: float     # m⁴ (torção)
    Wy: float    # m³ módulo elástico
    Wz: float
    circular: bool = False  # tensão de flexão = √(My² + Mz²)/W (senão |My|/Wy + |Mz|/Wz)


def tubo_circular(D: float, t: float) -> Secao:
    """Tubo de diâmetro externo D e espessura t (m)."""
    di = D - 2 * t
    a = math.pi / 4 * (D ** 2 - di ** 2)
    i = math.pi / 64 * (D ** 4 - di ** 4)
    return Secao(a, i, i, 2 * i, i / (D / 2), i / (D / 2), circular=True)


def retangulo(b: float, h: float) -> Secao:
    """Seção retangular maciça: b na direção y local, h na direção z local (m)."""
    a, c = max(b, h), min(b, h)
    j = a * c ** 3 * (1 / 3 - 0.21 * c / a * (1 - c ** 4 / (12 * a ** 4)))
    return Secao(b * h, b * h ** 3 / 12, h * b ** 3 / 12, j, b * h ** 2 / 6, h * b ** 2 / 6)


# ---------------------------------------------------------------- estrutura
@dataclass
class Estrutura:
    nos: torch.Tensor          # (Nn, 3) m
    barras: torch.Tensor       # (Nb, 2) índices dos nós
    props: torch.Tensor        # (Nb, 10): A Iy Iz J Wy Wz circular E G rho  — ver PROPS
    fy: torch.Tensor           # (Nb,) kN/m²
    trelica: torch.Tensor      # (Nb,) bool: só esforço axial
    apoios: torch.Tensor       # (Nn, 6) bool: grau de liberdade travado
    cargas: torch.Tensor       # (Nn, 6) kN e kN·m nos nós (eixos globais)
    carga_dist: torch.Tensor   # (Nb, 3) kN/m uniforme ao longo da barra (eixos globais)
    gravidade: float = 0.0     # m/s² para baixo (0 = sem peso próprio)

    @property
    def n_nos(self) -> int:
        return self.nos.shape[0]

    @property
    def n_barras(self) -> int:
        return self.barras.shape[0]

    @classmethod
    def criar(cls, nos, barras, secao: Secao | list[Secao] = None,
              material: Material | list[Material] = ACO_S235, trelica=False,
              apoios: dict | None = None, cargas: dict | None = None,
              carga_dist: dict | None = None, gravidade: float = 0.0) -> "Estrutura":
        """Atalho: seção/material/treliça únicos ou um por barra; apoios {nó: 6 bools},
        cargas {nó: (Fx, Fy, Fz[, Mx, My, Mz])}, carga_dist {barra: (wx, wy, wz)}."""
        nos = torch.as_tensor(nos, dtype=DT).reshape(-1, 3)
        barras = torch.as_tensor(barras, dtype=torch.long).reshape(-1, 2)
        nn, nb = nos.shape[0], barras.shape[0]
        secao = secao or tubo_circular(0.1143, 0.004)  # padrão do Karamba (RO114.3/4)
        secs = secao if isinstance(secao, (list, tuple)) else [secao] * nb
        mats = material if isinstance(material, (list, tuple)) else [material] * nb
        props = torch.tensor([[s.A, s.Iy, s.Iz, s.J, s.Wy, s.Wz, float(s.circular), m.E, m.G, m.rho]
                              for s, m in zip(secs, mats)], dtype=DT)
        fy = torch.tensor([m.fy for m in mats], dtype=DT)
        tr = torch.as_tensor(trelica, dtype=torch.bool).expand(nb).clone()
        ap = torch.zeros(nn, 6, dtype=torch.bool)
        for n, c in (apoios or {}).items():
            ap[n] = torch.as_tensor(c, dtype=torch.bool)
        cg = torch.zeros(nn, 6, dtype=DT)
        for n, c in (cargas or {}).items():
            c = torch.as_tensor(c, dtype=DT)
            cg[n, :c.numel()] += c
        cd = torch.zeros(nb, 3, dtype=DT)
        for b, c in (carga_dist or {}).items():
            cd[b] += torch.as_tensor(c, dtype=DT)
        return cls(nos, barras, props, fy, tr, ap, cg, cd, float(gravidade))

    def comprimentos(self) -> torch.Tensor:
        return (self.nos[self.barras[:, 1]] - self.nos[self.barras[:, 0]]).norm(dim=1)

    def massa(self) -> float:
        """kg"""
        return float((self.props[:, 0] * self.props[:, 9] * self.comprimentos()).sum())


PROPS = ("A", "Iy", "Iz", "J", "Wy", "Wz", "circular", "E", "G", "rho")


# ---------------------------------------------------------------- matrizes do elemento
def eixos_locais(d: torch.Tensor) -> torch.Tensor:
    """(E, 3, 3): linhas = eixos locais x, y, z em coordenadas globais."""
    zg = torch.tensor([0.0, 0, 1], dtype=d.dtype, device=d.device).expand_as(d)
    xg = torch.tensor([1.0, 0, 0], dtype=d.dtype, device=d.device).expand_as(d)
    y = torch.linalg.cross(zg, d)
    vertical = y.norm(dim=1) < 1e-9
    y_vert = torch.linalg.cross(xg, d)  # z = X global → y = z × x
    y = torch.where(vertical[:, None], y_vert, y)
    y = y / y.norm(dim=1, keepdim=True)
    z = torch.linalg.cross(d, y)
    return torch.stack([d, y, z], dim=1)


def _simetrizar(k):
    return k + k.transpose(1, 2) - torch.diag_embed(torch.diagonal(k, dim1=1, dim2=2))


def rigidez_local(L, A, Iy, Iz, J, E, G, trelica) -> torch.Tensor:
    """(E, 12, 12) matriz de rigidez no sistema local."""
    k = torch.zeros(L.shape[0], 12, 12, dtype=L.dtype, device=L.device)
    ea, gj = E * A / L, G * J / L
    k[:, 0, 0] = k[:, 6, 6] = ea
    k[:, 0, 6] = -ea
    viga = (~trelica).to(L.dtype)
    gj = gj * viga
    k[:, 3, 3] = k[:, 9, 9] = gj
    k[:, 3, 9] = -gj
    # flexão no plano xy (v, θz): 1, 5, 7, 11
    a, b, c = 12 * E * Iz / L ** 3 * viga, 6 * E * Iz / L ** 2 * viga, E * Iz / L * viga
    k[:, 1, 1] = k[:, 7, 7] = a
    k[:, 1, 7] = -a
    k[:, 1, 5] = k[:, 1, 11] = b
    k[:, 5, 7] = k[:, 7, 11] = -b
    k[:, 5, 5] = k[:, 11, 11] = 4 * c
    k[:, 5, 11] = 2 * c
    # flexão no plano xz (w, θy): 2, 4, 8, 10
    a, b, c = 12 * E * Iy / L ** 3 * viga, 6 * E * Iy / L ** 2 * viga, E * Iy / L * viga
    k[:, 2, 2] = k[:, 8, 8] = a
    k[:, 2, 8] = -a
    k[:, 2, 4] = k[:, 2, 10] = -b
    k[:, 4, 8] = k[:, 8, 10] = b
    k[:, 4, 4] = k[:, 10, 10] = 4 * c
    k[:, 4, 10] = 2 * c
    return _simetrizar(k)


def rigidez_geometrica_local(L, N, A, Iy, Iz, trelica) -> torch.Tensor:
    """(E, 12, 12) matriz geométrica (N > 0 = tração), para flambagem linear."""
    kg = torch.zeros(L.shape[0], 12, 12, dtype=L.dtype, device=L.device)
    viga = ~trelica
    p = N / L
    # treliça: só translações transversais
    for i, j in ((1, 7), (2, 8)):
        kg[:, i, i] = kg[:, j, j] = 1.0
        kg[:, i, j] = -1.0
    v = viga.to(L.dtype)
    lv = L * v
    for i, j in ((1, 7), (2, 8)):  # vigas: 6/5 no lugar de 1
        kg[:, i, i] = kg[:, j, j] = 1 + v / 5
        kg[:, i, j] = -(1 + v / 5)
    kg[:, 1, 5] = kg[:, 1, 11] = lv / 10
    kg[:, 5, 7] = kg[:, 7, 11] = -lv / 10
    kg[:, 2, 4] = kg[:, 2, 10] = -lv / 10
    kg[:, 4, 8] = kg[:, 8, 10] = lv / 10
    kg[:, 5, 5] = kg[:, 11, 11] = kg[:, 4, 4] = kg[:, 10, 10] = 2 * L * lv / 15
    kg[:, 5, 11] = kg[:, 4, 10] = -L * lv / 30
    ip = (Iy + Iz) / A * v
    kg[:, 3, 3] = kg[:, 9, 9] = ip
    kg[:, 3, 9] = -ip
    return _simetrizar(kg) * p[:, None, None]


def _bloco(R):
    T = torch.zeros(R.shape[0], 12, 12, dtype=R.dtype, device=R.device)
    for i in range(4):
        T[:, 3 * i:3 * i + 3, 3 * i:3 * i + 3] = R
    return T


def cargas_equivalentes_local(L, w, trelica) -> torch.Tensor:
    """(E, 12) forças nodais equivalentes de carga uniforme w (E, 3) local."""
    f = torch.zeros(L.shape[0], 12, dtype=L.dtype, device=L.device)
    f[:, 0:3] = f[:, 6:9] = w * L[:, None] / 2
    m = L ** 2 / 12 * (~trelica).to(L.dtype)
    f[:, 4], f[:, 10] = -w[:, 2] * m, w[:, 2] * m
    f[:, 5], f[:, 11] = w[:, 1] * m, -w[:, 1] * m
    return f


# ---------------------------------------------------------------- preparação do lote
@dataclass
class _Lote:
    D: int                     # graus de liberdade por variante (completados)
    xi: torch.Tensor           # (E, 3)
    xj: torch.Tensor
    props: torch.Tensor        # (E, 10)
    fy: torch.Tensor
    trelica: torch.Tensor
    w: torch.Tensor            # (E, 3) carga distribuída global
    variante: torch.Tensor     # (E,)
    dofs: torch.Tensor         # (E, 12) dentro da variante
    barra: torch.Tensor        # (E,) barra original
    primeiro: torch.Tensor     # (E,) bool: 1º sub-elemento da barra
    ultimo: torch.Tensor
    F: torch.Tensor            # (V, D)
    travado: torch.Tensor      # (V, D) bool
    gravidade: torch.Tensor    # (V,)
    n_nos: list = field(default_factory=list)
    n_barras: list = field(default_factory=list)


def _preparar(estruturas: list[Estrutura], n_sub: int, device) -> _Lote:
    partes = {k: [] for k in ("xi", "xj", "props", "fy", "trelica", "w", "variante", "dofs",
                              "barra", "primeiro", "ultimo")}
    n_tot, F, trav = [], [], []
    ar6 = torch.arange(6)
    for v, s in enumerate(estruturas):
        nn = s.n_nos
        nos = s.nos.to(DT)
        sub = torch.where(s.trelica, 1, n_sub)
        k = int(sub.max()) if s.n_barras else 1
        # cadeia de nós de cada barra: [i, internos..., j] (internos só onde sub = k > 1)
        cadeia = torch.full((s.n_barras, k + 1), -1, dtype=torch.long)
        cadeia[:, 0] = s.barras[:, 0]
        novos = []
        m = sub > 1
        if k > 1 and m.any():
            ids = nn + torch.arange(int(m.sum()) * (k - 1)).reshape(-1, k - 1)
            t = torch.arange(1, k, dtype=DT) / k
            pi, pj = nos[s.barras[m, 0]], nos[s.barras[m, 1]]
            novos.append((pi[:, None] + t[None, :, None] * (pj - pi)[:, None]).reshape(-1, 3))
            cadeia[m, 1:k] = ids
            cadeia[m, k] = s.barras[m, 1]
        cadeia[~m, 1] = s.barras[~m, 1]
        todos = torch.cat([nos] + novos)
        ni, nj, bar, pos = [], [], [], []
        for e in range(k):
            ok = cadeia[:, e + 1] >= 0
            ni.append(cadeia[ok, e])
            nj.append(cadeia[ok, e + 1])
            bar.append(ok.nonzero().flatten())
            pos.append(torch.full((int(ok.sum()),), e))
        ni, nj, bar, pos = map(torch.cat, (ni, nj, bar, pos))
        partes["xi"].append(todos[ni])
        partes["xj"].append(todos[nj])
        partes["props"].append(s.props[bar].to(DT))
        partes["fy"].append(s.fy[bar].to(DT))
        partes["trelica"].append(s.trelica[bar])
        partes["w"].append(s.carga_dist[bar].to(DT))
        partes["variante"].append(torch.full((bar.numel(),), v))
        partes["dofs"].append(torch.cat([ni[:, None] * 6 + ar6, nj[:, None] * 6 + ar6], dim=1))
        partes["barra"].append(bar)
        partes["primeiro"].append(pos == 0)
        partes["ultimo"].append(pos == sub[bar] - 1)
        n_tot.append(todos.shape[0])
        F.append(s.cargas.to(DT).flatten())
        trav.append(torch.cat([s.apoios.flatten(),
                               torch.zeros(6 * (todos.shape[0] - nn), dtype=torch.bool)]))
    D = 6 * max(n_tot)

    def completar(x, valor):
        return torch.stack([torch.cat([a, a.new_full((D - a.numel(),), valor)]) for a in x])

    cat = {k: torch.cat(v).to(device) for k, v in partes.items()}
    return _Lote(D=D, F=completar(F, 0.0).to(device), travado=completar(trav, True).to(device),
                 gravidade=torch.tensor([s.gravidade for s in estruturas], dtype=DT, device=device),
                 n_nos=[s.n_nos for s in estruturas], n_barras=[s.n_barras for s in estruturas],
                 **cat)


# ---------------------------------------------------------------- solução
@dataclass
class Resultado:
    estavel: torch.Tensor        # (V,) bool (False = mecanismo / matriz singular)
    desloc_max: torch.Tensor     # (V,) m, maior translação nodal
    energia: torch.Tensor        # (V,) kN·m, energia de deformação ½ uᵀF
    massa: torch.Tensor          # (V,) kg
    utilizacao: torch.Tensor     # (V,) máx. σ/fy (σ = |N|/A + flexão elástica)
    flambagem: torch.Tensor | None = None   # (V,) 1º fator de carga de flambagem
    deslocamentos: list | None = None       # [(Nn, 6)] por variante (nós originais)
    esforcos: list | None = None            # [(Nb, 2, 6)] N Vy Vz Mt My Mz no início e fim


def _montar(lote: _Lote, ke: torch.Tensor, V: int) -> torch.Tensor:
    D = lote.D
    base = (lote.variante * D)[:, None] + lote.dofs                     # (E, 12)
    idx = (base[:, :, None] * D + lote.dofs[:, None, :]).flatten()      # (E·144,)
    K = torch.zeros(V * D * D, dtype=DT, device=ke.device)
    K.index_add_(0, idx, ke.flatten())
    return K.view(V, D, D)


def _aplicar_apoios(K, travado):
    livre = (~travado).to(K.dtype)
    return K * livre[:, :, None] * livre[:, None, :] + torch.diag_embed(travado.to(K.dtype))


def resolver(estruturas: list[Estrutura], n_sub: int = 1, flambagem: bool = False,
             detalhes: bool = False, device: str | None = None,
             memoria_max: float = 1.5e9) -> Resultado:
    """Resolve todas as variantes (análise linear de 1ª ordem, um caso de carga).

    n_sub: elementos por barra de pórtico (treliças não são subdivididas); afeta
    a flambagem e os deslocamentos no meio das barras (nos nós o resultado é exato).
    """
    device = device or ("cuda" if torch.cuda.is_available() else "cpu")
    # lotes de variantes para caber na memória (matrizes densas D × D)
    d_est = [6 * (s.n_nos + (n_sub - 1) * int((~s.trelica).sum())) for s in estruturas]
    partes, ini = [], 0
    while ini < len(estruturas):
        fim, dmax = ini, 0
        while fim < len(estruturas):
            d = max(dmax, d_est[fim])
            if fim > ini and (fim - ini + 1) * d * d * 8 * (3 if flambagem else 2) > memoria_max:
                break
            dmax, fim = d, fim + 1
        partes.append(_resolver_lote(estruturas[ini:fim], n_sub, flambagem, detalhes, device))
        ini = fim
    if len(partes) == 1:
        return partes[0]
    junta = {}
    for nome in ("estavel", "desloc_max", "energia", "massa", "utilizacao", "flambagem"):
        vals = [getattr(p, nome) for p in partes]
        junta[nome] = None if vals[0] is None else torch.cat(vals)
    for nome in ("deslocamentos", "esforcos"):
        vals = [getattr(p, nome) for p in partes]
        junta[nome] = None if vals[0] is None else sum(vals, [])
    return Resultado(**junta)


def _resolver_lote(estruturas, n_sub, flambagem, detalhes, device) -> Resultado:
    V = len(estruturas)
    lt = _preparar(estruturas, n_sub, device)
    p = lt.props
    A, Iy, Iz, J, Wy, Wz, circ, E, G, rho = p.unbind(1)
    dx = lt.xj - lt.xi
    L = dx.norm(dim=1)
    R = eixos_locais(dx / L[:, None])
    T = _bloco(R)
    kl = rigidez_local(L, A, Iy, Iz, J, E, G, lt.trelica)
    ke = T.transpose(1, 2) @ kl @ T

    # cargas: nós + distribuídas + peso próprio (−z)
    w = lt.w.clone()
    w[:, 2] -= rho * A * lt.gravidade[lt.variante] / 1000.0      # kg/m³·m²·m/s² = N/m → kN/m
    wl = (R @ w[:, :, None]).squeeze(2)
    feq_l = cargas_equivalentes_local(L, wl, lt.trelica)
    feq = (T.transpose(1, 2) @ feq_l[:, :, None]).squeeze(2)
    F = lt.F.clone()
    F.view(-1).index_add_(0, ((lt.variante * lt.D)[:, None] + lt.dofs).flatten(), feq.flatten())

    K = _montar(lt, ke, V)
    diag = torch.diagonal(K, dim1=1, dim2=2)
    rot = (torch.arange(lt.D, device=device) % 6 >= 3)
    travado = lt.travado | ((diag == 0) & rot)   # rotações de nós só de treliça
    F = F * (~travado)
    K = _aplicar_apoios(K, travado)
    escala = torch.diagonal(K, dim1=1, dim2=2).amax(1)
    Lc, info = torch.linalg.cholesky_ex(K)
    piv = torch.diagonal(Lc, dim1=1, dim2=2) ** 2
    estavel = (info == 0) & torch.nan_to_num(piv, nan=0.0).amin(1).gt(1e-11 * escala)
    eye = torch.eye(lt.D, dtype=DT, device=device)
    Lc = torch.where(estavel[:, None, None], Lc, eye)
    u = torch.cholesky_solve(F[:, :, None], Lc).squeeze(2)

    nan = torch.tensor(float("nan"), dtype=DT, device=device)
    energia = torch.where(estavel, 0.5 * (u * F).sum(1), nan)
    desloc = torch.where(estavel, u.view(V, -1, 6)[..., :3].norm(dim=2).amax(1), nan)
    massa = torch.zeros(V, dtype=DT, device=device).index_add_(0, lt.variante, rho * A * L)

    # esforços nas seções: S(x) = −F_i − w x ;  M(x) = −M_i + x (e1 × F_i) + x²/2 (e1 × w)
    ue = u[lt.variante[:, None], lt.dofs]
    fl = (kl @ (T @ ue[:, :, None])).squeeze(2) - feq_l
    Fi, Mi = fl[:, 0:3], fl[:, 3:6]

    def e1x(a):
        return torch.stack([torch.zeros_like(a[..., 0]), -a[..., 2], a[..., 1]], dim=-1)

    x = torch.tensor(PONTOS_ESFORCO, dtype=DT, device=device)[None, :, None] * L[:, None, None]
    SF = -Fi[:, None] - wl[:, None] * x
    SM = -Mi[:, None] + x * e1x(Fi)[:, None] + x ** 2 / 2 * e1x(wl)[:, None]
    S = torch.cat([SF, SM], dim=2)                                     # (E, P, 6)
    flex_circ = torch.sqrt(S[..., 4] ** 2 + S[..., 5] ** 2) / Wy[:, None]
    flex_ret = S[..., 4].abs() / Wy[:, None] + S[..., 5].abs() / Wz[:, None]
    sigma = S[..., 0].abs() / A[:, None] + torch.where(circ[:, None] > 0, flex_circ, flex_ret)
    util_e = (sigma / lt.fy[:, None]).amax(1)
    util = torch.zeros(V, dtype=DT, device=device).scatter_reduce(
        0, lt.variante, util_e, reduce="amax", include_self=False)
    util = torch.where(estavel, util, nan)

    fator = None
    if flambagem:
        Nm = (S[:, 0, 0] + S[:, -1, 0]) / 2
        kg = rigidez_geometrica_local(L, Nm, A, Iy, Iz, lt.trelica)
        Kg = _montar(lt, T.transpose(1, 2) @ kg @ T, V)
        livre = (~travado).to(DT)
        Kg = -Kg * livre[:, :, None] * livre[:, None, :]
        Y = torch.linalg.solve_triangular(Lc, Kg, upper=False)
        C = torch.linalg.solve_triangular(Lc, Y.transpose(1, 2), upper=False).transpose(1, 2)
        mu = torch.linalg.eigvalsh((C + C.transpose(1, 2)) / 2)[:, -1]
        fator = torch.where(mu > 1e-12, 1 / mu.clamp(min=1e-300), torch.full_like(mu, float("inf")))
        fator = torch.where(estavel, fator, nan)

    desl_l = esf_l = None
    if detalhes:
        desl_l, esf_l = [], []
        for v in range(V):
            desl_l.append(u[v, :6 * lt.n_nos[v]].view(-1, 6).cpu())
            mv = lt.variante == v
            e = torch.zeros(lt.n_barras[v], 2, 6, dtype=DT, device=device)
            a, b = mv & lt.primeiro, mv & lt.ultimo
            e[lt.barra[a], 0] = S[a, 0]
            e[lt.barra[b], 1] = S[b, -1]
            esf_l.append(e.cpu())
    return Resultado(estavel, desloc, energia, massa, util, fator, desl_l, esf_l)

"""Motor B do sol_luz: luz natural com reflexões (coeficientes de luz natural).

Mesmo esquema do "annual daylight" do Honeybee (Radiance, 2 fases + sol):
  E(s, h) = Σ_p C_dir[s, p] · L_dif[p, h]          céu difuso visto direto
          + Σ_p C_ref[s, p] · L_tot[p, h]          céu + sol por reflexões
          + E_sol(s, h)                            sol direto exato (sem patches)
  C_dir = coeficientes de visão direta (0 reflexões, inclui vidro)
  C_ref = coeficientes com 1 ou mais reflexões difusas
A última coluna dos coeficientes é o solo (fora da cena), com luminância
uniforme L_solo = ρ_solo · E_horiz / π.

Coeficientes por path tracing na GPU (Warp): de cada sensor saem N raios com
distribuição cosseno; o raio reflete de forma difusa nas superfícies opacas
(refletância ρ da face) e atravessa vidros em linha reta, atenuado por τ(θ)
(Fresnel, n = 1,52, como o material glass do Radiance). Ao escapar, soma π/N
vezes o peso acumulado na coluna do patch de onde veio.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import torch
import warp as wp

from otimiza.nucleo.ceu import Ceu
from otimiza.nucleo.matriz_ceu import MatrizCeu
from otimiza.nucleo.raios import Cena

N_VIDRO = 1.52
GRAUS = wp.constant(57.29577951308232)
EPS = wp.constant(1e-4)


# ------------------------------------------------------------------ vidro
def absorcao_vidro(tau_normal: float) -> float:
    """Fator a0 da placa (transmitância interna na normal) que dá τ(0) = tau_normal."""
    r0 = ((N_VIDRO - 1) / (N_VIDRO + 1)) ** 2
    if tau_normal <= 0:
        return 0.0
    # τ = (1−R)²·a / (1 − R²·a²)  ->  τR²·a² + (1−R)²·a − τ = 0
    A, B, C = tau_normal * r0**2, (1 - r0) ** 2, -tau_normal
    a0 = (-B + math.sqrt(B * B - 4 * A * C)) / (2 * A)
    return min(a0, 1.0)


@wp.func
def transmitancia_vidro(cos_i: float, a0: float) -> float:
    """Placa de vidro com reflexões múltiplas internas (Fresnel não polarizado)."""
    ci = wp.clamp(wp.abs(cos_i), 1e-4, 1.0)
    si = wp.sqrt(1.0 - ci * ci)
    st = si / 1.52
    ct = wp.sqrt(1.0 - st * st)
    rs = (ci - 1.52 * ct) / (ci + 1.52 * ct)
    rp = (1.52 * ci - ct) / (1.52 * ci + ct)
    r = 0.5 * (rs * rs + rp * rp)
    a = wp.pow(a0, 1.0 / ct)
    return (1.0 - r) * (1.0 - r) * a / (1.0 - r * r * a * a)


# ------------------------------------------------------------------ auxiliares
@wp.func
def direcao_cosseno(n: wp.vec3, u1: float, u2: float) -> wp.vec3:
    r = wp.sqrt(u1)
    phi = 6.283185307179586 * u2
    x = r * wp.cos(phi)
    y = r * wp.sin(phi)
    z = wp.sqrt(wp.max(0.0, 1.0 - u1))
    # base ortonormal em torno de n
    a = wp.vec3(1.0, 0.0, 0.0)
    if wp.abs(n[0]) > 0.9:
        a = wp.vec3(0.0, 1.0, 0.0)
    t = wp.normalize(wp.cross(a, n))
    b = wp.cross(n, t)
    return wp.normalize(t * x + b * y + n * z)


@wp.func
def indice_patch(d: wp.vec3, alpha: float, n_faixas: int,
                 npf: wp.array(dtype=wp.int32), ini: wp.array(dtype=wp.int32)) -> int:
    alt = wp.asin(wp.clamp(d[2], -1.0, 1.0)) * GRAUS
    f = wp.clamp(int(wp.floor(alt / alpha)), 0, n_faixas - 1)
    n = npf[f]
    nf = float(n)
    az = wp.atan2(d[0], d[1]) * GRAUS
    if az < 0.0:
        az = az + 360.0
    j = int(wp.floor((az + 180.0 / nf) / (360.0 / nf))) % n
    return ini[f] + j


# ------------------------------------------------------------------ kernels
@wp.kernel
def _k_coeficientes(ids: wp.array(dtype=wp.uint64), var: wp.array(dtype=wp.int32),
                    loc: wp.array(dtype=wp.int32), base_face: wp.array(dtype=wp.int32),
                    rho: wp.array(dtype=float), a0_vidro: wp.array(dtype=float),
                    orig: wp.array(dtype=wp.vec3), nrm: wp.array(dtype=wp.vec3),
                    n_raios: int, max_reflexoes: int, semente: int,
                    alpha: float, n_faixas: int,
                    npf: wp.array(dtype=wp.int32), ini: wp.array(dtype=wp.int32), col_solo: int,
                    c_dir: wp.array2d(dtype=float), c_ref: wp.array2d(dtype=float)):
    s, k = wp.tid()
    estado = wp.rand_init(semente, loc[s] * n_raios + k)   # índice local: independe do lote
    malha = ids[var[s]]
    b0 = base_face[var[s]]
    o = orig[s]
    d = direcao_cosseno(nrm[s], wp.randf(estado), wp.randf(estado))
    peso = float(1.0)
    refl = int(0)
    contrib = 3.141592653589793 / float(n_raios)
    passos = int(0)
    while passos < 64:
        passos += 1
        q = wp.mesh_query_ray(malha, o + d * EPS, d, 1.0e6)
        if not q.result:
            col = col_solo
            if d[2] > 0.0:
                col = indice_patch(d, alpha, n_faixas, npf, ini)
            if refl == 0:
                wp.atomic_add(c_dir, s, col, peso * contrib)
            else:
                wp.atomic_add(c_ref, s, col, peso * contrib)
            return
        f = b0 + q.face
        p = o + d * (q.t + EPS)
        a0 = a0_vidro[f]
        if a0 > 0.0:  # vidro: atravessa reto
            peso = peso * transmitancia_vidro(wp.dot(d, q.normal), a0)
            o = p
        else:         # opaco: reflexão difusa
            if refl >= max_reflexoes:
                return
            peso = peso * rho[f]
            if peso < 1.0e-6:
                return
            n = q.normal
            if wp.dot(n, d) > 0.0:
                n = -n
            refl += 1
            o = p + n * EPS
            d = direcao_cosseno(n, wp.randf(estado), wp.randf(estado))


@wp.kernel
def _k_sol_direto(ids: wp.array(dtype=wp.uint64), var: wp.array(dtype=wp.int32),
                  base_face: wp.array(dtype=wp.int32), a0_vidro: wp.array(dtype=float),
                  orig: wp.array(dtype=wp.vec3), nrm: wp.array(dtype=wp.vec3),
                  sol: wp.array(dtype=wp.vec3), saida: wp.array2d(dtype=float)):
    """Fator de sol direto (s, t) = τ do caminho · max(0, n·sol); 0 se bloqueado."""
    s, t = wp.tid()
    d = wp.normalize(sol[t])
    c = wp.dot(nrm[s], d)
    if c <= 0.0:
        saida[s, t] = 0.0
        return
    malha = ids[var[s]]
    b0 = base_face[var[s]]
    o = orig[s]
    peso = c
    passos = int(0)
    while passos < 16:
        passos += 1
        q = wp.mesh_query_ray(malha, o + d * EPS, d, 1.0e6)
        if not q.result:
            saida[s, t] = peso
            return
        a0 = a0_vidro[b0 + q.face]
        if a0 <= 0.0:
            saida[s, t] = 0.0
            return
        peso = peso * transmitancia_vidro(wp.dot(d, q.normal), a0)
        o = o + d * (q.t + EPS)
    saida[s, t] = 0.0


# ------------------------------------------------------------------ interface
@dataclass
class Materiais:
    """Por face de cada variante (concatenadas na ordem das variantes).

    Valores fotópicos (luz visível). Os melanópicos são opcionais: se None, o
    material é neutro (mesma refletância/transmitância nos dois canais).
    """
    refletancia: torch.Tensor     # (F_total,) 0..1, faces opacas
    transmitancia: torch.Tensor   # (F_total,) τ normal; > 0 = vidro
    base_face: torch.Tensor       # (V,) índice da 1ª face de cada variante
    refletancia_mel: torch.Tensor | None = None
    transmitancia_mel: torch.Tensor | None = None

    @staticmethod
    def de_listas(listas: list[tuple[torch.Tensor, torch.Tensor]]) -> "Materiais":
        """listas = [(refletancia, transmitancia) por variante]."""
        base, acc = [], 0
        for r, _ in listas:
            base.append(acc)
            acc += r.shape[0]
        return Materiais(torch.cat([r for r, _ in listas]).float(),
                         torch.cat([t for _, t in listas]).float(),
                         torch.tensor(base, dtype=torch.int32))

    def melanopico(self) -> "Materiais":
        """Mesmos materiais com as propriedades do canal melanópico."""
        r = self.refletancia if self.refletancia_mel is None else self.refletancia_mel.float()
        t = self.transmitancia if self.transmitancia_mel is None else self.transmitancia_mel.float()
        return Materiais(r, t, self.base_face)


@dataclass
class Coeficientes:
    direto: torch.Tensor     # (S, P + 1) visão direta (última coluna = solo)
    refletido: torch.Tensor  # (S, P + 1) com ≥ 1 reflexão


def _wp(t, device, dtype=None):
    return wp.from_torch(t.to(device=device).contiguous(), dtype=dtype)


def coeficientes_luz(cena: Cena, materiais: Materiais, ceu: Ceu, pontos, normais, variante=None,
                     n_raios: int = 4096, max_reflexoes: int = 4, semente: int = 1) -> Coeficientes:
    dev = cena.device
    S, P = pontos.shape[0], ceu.n_patches
    var = cena._variantes(variante, S)
    a0 = torch.tensor([absorcao_vidro(t) for t in materiais.transmitancia.tolist()],
                      dtype=torch.float32)
    c_dir = torch.zeros(S, P + 1, dtype=torch.float32, device=dev)
    c_ref = torch.zeros(S, P + 1, dtype=torch.float32, device=dev)
    wp.launch(_k_coeficientes, dim=(S, n_raios), device=dev, inputs=[
        cena.ids, var, cena._indice_local(variante, S), _wp(materiais.base_face, dev),
        _wp(materiais.refletancia.float(), dev), _wp(a0, dev),
        _wp(pontos.float(), dev, wp.vec3), _wp(normais.float(), dev, wp.vec3),
        n_raios, max_reflexoes, semente,
        float(ceu.alpha), int(ceu.n_por_faixa.shape[0]),
        _wp(ceu.n_por_faixa.int(), dev), _wp(ceu.inicio_faixa.int(), dev), P,
        wp.from_torch(c_dir), wp.from_torch(c_ref)])
    return Coeficientes(c_dir, c_ref)


def fator_sol_direto(cena: Cena, materiais: Materiais, pontos, normais, vetores_sol, variante=None):
    """(S, T) τ·cos do sol direto em cada sensor (0 se bloqueado por opaco)."""
    dev = cena.device
    S, T = pontos.shape[0], vetores_sol.shape[0]
    var = cena._variantes(variante, S)
    a0 = torch.tensor([absorcao_vidro(t) for t in materiais.transmitancia.tolist()],
                      dtype=torch.float32)
    saida = torch.zeros(S, T, dtype=torch.float32, device=dev)
    wp.launch(_k_sol_direto, dim=(S, T), device=dev, inputs=[
        cena.ids, var, _wp(materiais.base_face, dev), _wp(a0, dev),
        _wp(pontos.float(), dev, wp.vec3), _wp(normais.float(), dev, wp.vec3),
        _wp(vetores_sol.float(), dev, wp.vec3), wp.from_torch(saida)])
    return saida


def iluminancia_anual(coef: Coeficientes, matriz_luz: MatrizCeu, fator_sol: torch.Tensor,
                      iluminancia_normal_sol: torch.Tensor, horas_sol: torch.Tensor,
                      refletancia_solo: float = 0.2, so_sol_direto: bool = False,
                      der_ceu: float = 1.0, der_sol: float = 1.0) -> torch.Tensor:
    """(S, H) lux (fotópico) ou m-EDI (melanópico, ver `iluminancia_melanopica_anual`).

    matriz_luz: matriz de céu com luz=True (cd/m²).
    fator_sol: (S, T) de `fator_sol_direto` para as T horas em `horas_sol` (índices).
    iluminancia_normal_sol: (H,) lux do sol direto (DNI · K_dir).
    so_sol_direto: só o sol direto (para o ASE).
    der_ceu, der_sol: razão de eficácia melanópica (DER, relativa ao D65) do céu
      difuso e do sol; 1,0 = fotópico / espectro igual ao D65.
    """
    dev = coef.direto.device
    H = matriz_luz.difusa.shape[1]
    e = torch.zeros(coef.direto.shape[0], H, dtype=torch.float32, device=dev)
    e[:, horas_sol] = fator_sol * der_sol * \
        iluminancia_normal_sol.to(device=dev, dtype=torch.float32)[horas_sol]
    if so_sol_direto:
        return e
    ceu = matriz_luz.ceu
    om = ceu.angulo_solido.to(dev)
    cz = ceu.vetores[:, 2].to(dev)

    def com_solo(L):  # acrescenta a linha do solo: ρ · E_horiz / π
        L = L.to(device=dev, dtype=torch.float64)
        e_h = (L * (om * cz)[:, None]).sum(0, keepdim=True)
        return torch.cat([L, refletancia_solo * e_h / math.pi]).float()

    dif = com_solo(matriz_luz.difusa * der_ceu)
    tot = com_solo(matriz_luz.difusa * der_ceu + matriz_luz.direta * der_sol)
    # sol na visão direta: só pelo cálculo exato acima; o sol direto que chega
    # ao solo de fora entra pela linha do solo do céu total
    dif_mais_solo_do_sol = dif.clone()
    dif_mais_solo_do_sol[-1] = tot[-1]
    return e + coef.direto @ dif_mais_solo_do_sol + coef.refletido @ tot


# ------------------------------------------------------------------ melanópico (1d)
EML_POR_MEDI = 1.104  # EML (WELL) = 1,104 × m-EDI (CIE S 026); para D65, m-EDI = lux


def iluminancia_melanopica_anual(coef_mel: Coeficientes, matriz_luz: MatrizCeu,
                                 fator_sol_mel: torch.Tensor, iluminancia_normal_sol,
                                 horas_sol, der_ceu: float = 1.0, der_sol: float = 1.0,
                                 refletancia_solo_mel: float = 0.2) -> torch.Tensor:
    """(S, H) m-EDI em lux (iluminância melanópica equivalente D65, CIE S 026).

    coef_mel e fator_sol_mel calculados com `materiais.melanopico()`.
    der_ceu / der_sol: DER do céu difuso e do sol. Padrão 1,0 (= D65): sem dados
    de espectro, supõe-se luz do tipo D65; passe valores medidos ou da literatura.
    """
    return iluminancia_anual(coef_mel, matriz_luz, fator_sol_mel, iluminancia_normal_sol,
                             horas_sol, refletancia_solo=refletancia_solo_mel,
                             der_ceu=der_ceu, der_sol=der_sol)


def eml(m_edi: torch.Tensor) -> torch.Tensor:
    """Equivalent Melanopic Lux (WELL v2) a partir da m-EDI."""
    return m_edi * EML_POR_MEDI


def sensores_olho(pontos_xy: torch.Tensor, altura: float = 1.2, n_direcoes: int = 4):
    """Sensores verticais na altura dos olhos: (S·n, 3) pontos e normais
    horizontais (padrão N, L, S, O). Retorna também o índice do ponto de origem."""
    ang = torch.arange(n_direcoes, dtype=torch.float64) * (2 * math.pi / n_direcoes)
    dirs = torch.stack([torch.sin(ang), torch.cos(ang), torch.zeros_like(ang)], dim=1)
    p = pontos_xy.to(torch.float64)
    p3 = torch.cat([p[:, :2], torch.full((p.shape[0], 1), altura, dtype=torch.float64)], dim=1)
    pontos = p3.repeat_interleave(n_direcoes, dim=0)
    normais = dirs.repeat(p.shape[0], 1)
    origem = torch.arange(p.shape[0]).repeat_interleave(n_direcoes)
    return pontos, normais, origem


def metricas_circadianas(m_edi: torch.Tensor, clima, limite_medi: float = 250.0,
                         horas_min_por_dia: int = 4, inicio: float = 8.0, fim: float = 18.0):
    """Por sensor:
      horas_pct — % das horas ocupadas com m-EDI ≥ limite
      dias_pct  — % dos dias com pelo menos `horas_min_por_dia` horas ≥ limite
    Limite padrão 250 lux m-EDI (Brown et al. 2022, recomendação para o dia).
    """
    occ = horas_ocupadas(clima, inicio, fim).to(m_edi.device)
    acima = (m_edi >= limite_medi) & occ[None, :]
    n_dias = m_edi.shape[1] // 24
    por_dia = acima[:, : n_dias * 24].reshape(m_edi.shape[0], n_dias, 24).sum(dim=2)
    return {
        "horas_pct": acima.float().sum(1) / occ.sum() * 100,
        "dias_pct": (por_dia >= horas_min_por_dia).float().mean(1) * 100,
    }


# ------------------------------------------------------------------ métricas
def horas_ocupadas(clima, inicio: float = 8.0, fim: float = 18.0) -> torch.Tensor:
    """(H,) bool: horas cujo meio cai entre inicio e fim (padrão do Honeybee: 8–18 h)."""
    m = clima.hora_meio
    return (m >= inicio) & (m < fim)


def metricas_anuais(e: torch.Tensor, ocupadas: torch.Tensor, limite_da: float = 300.0,
                    udi_min: float = 100.0, udi_max: float = 3000.0) -> dict[str, torch.Tensor]:
    """DA, UDI (baixo, útil, alto) em % das horas ocupadas, por sensor."""
    occ = e[:, ocupadas.to(e.device)]
    n = occ.shape[1]
    return {
        "da": (occ >= limite_da).float().sum(1) / n * 100,
        "udi_baixo": (occ < udi_min).float().sum(1) / n * 100,
        "udi": ((occ >= udi_min) & (occ <= udi_max)).float().sum(1) / n * 100,
        "udi_alto": (occ > udi_max).float().sum(1) / n * 100,
    }


def sda(da: torch.Tensor, areas: torch.Tensor | None = None, limite: float = 50.0) -> float:
    """% da área com DA ≥ limite (LM-83: DA300 em ≥ 50% das horas)."""
    ok = (da >= limite).float()
    if areas is None:
        return ok.mean().item() * 100
    a = areas.to(ok)
    return ((ok * a).sum() / a.sum()).item() * 100


def ase(e_sol_direto: torch.Tensor, ocupadas: torch.Tensor, areas=None,
        limite_lux: float = 1000.0, limite_horas: int = 250) -> float:
    """% da área com sol direto > 1000 lux por mais de 250 horas ocupadas (LM-83)."""
    horas = (e_sol_direto[:, ocupadas.to(e_sol_direto.device)] > limite_lux).sum(1)
    ok = (horas > limite_horas).float()
    if areas is None:
        return ok.mean().item() * 100
    a = areas.to(ok)
    return ((ok * a).sum() / a.sum()).item() * 100


def luminancia_ceu_cie_encoberto(ceu: Ceu, e_horiz: float = 10_000.0) -> torch.Tensor:
    """(P + 1,) céu CIE encoberto: L = Lz (1 + 2 sen alt) / 3, com Σ L·Ω·cos = e_horiz.
    Última posição = solo com refletância 0,2."""
    alt = torch.deg2rad(ceu.altitude)
    rel = (1 + 2 * torch.sin(alt)) / 3
    escala = e_horiz / (rel * ceu.angulo_solido * ceu.vetores[:, 2]).sum()
    L = rel * escala
    return torch.cat([L, torch.tensor([0.2 * e_horiz / math.pi], dtype=L.dtype)])


def fator_luz_diurna(coef: Coeficientes, ceu: Ceu, refletancia_solo: float = 0.2) -> torch.Tensor:
    """(S,) DF em % sob céu CIE encoberto (iluminância interna / externa horizontal)."""
    e_ext = 10_000.0
    L = luminancia_ceu_cie_encoberto(ceu, e_ext)
    L[-1] = refletancia_solo * e_ext / math.pi
    L = L.to(device=coef.direto.device, dtype=torch.float32)
    return (coef.direto + coef.refletido) @ L / e_ext * 100

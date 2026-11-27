"""Motor C do sol_luz: imagem olho-de-peixe e DGP (Wienold & Christoffersen 2006).

Cada vista (olho + direção do olhar) vira uma imagem olho-de-peixe angular de
180° (n × n pixels). Como no motor B, cada pixel tem coeficientes por patch do
céu, separados em visão direta (C_dir) e com reflexões (C_ref):
  L_pixel(h) = C_dir · L_céu_difuso(h) + C_ref · L_céu_total(h)
O sol direto entra à parte, como fonte do tamanho real (0,533°).

DGP = 5,87e-5·Ev + 9,18e-2·log10(1 + Σ Ls²·ωs / (Ev^1,87·P²)) + 0,16
  Ev  = iluminância vertical no olho (Σ L·ω·cos θ)
  fontes = pixels acima do limiar (padrão 2000 cd/m², como o dcglare/HB
           imageless; < 100 = fator × luminância média da imagem)
  P   = índice de posição: Guth acima da linha de visão, Iwata abaixo
Com Ev < 1000 lux, correção de baixa luz de Wienold (2009), opcional.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import torch
import warp as wp

from otimiza.modulos.sol_luz.motor_b import (
    EPS, Materiais, absorcao_vidro, direcao_cosseno, fator_sol_direto, indice_patch,
    transmitancia_vidro,
)
from otimiza.nucleo.ceu import Ceu
from otimiza.nucleo.matriz_ceu import MatrizCeu
from otimiza.nucleo.raios import Cena

OMEGA_SOL = 2 * math.pi * (1 - math.cos(math.radians(0.533 / 2)))  # ≈ 6,8e-5 sr


# ------------------------------------------------------------------ geometria da imagem
@dataclass
class Olhos:
    n: int                   # resolução da imagem (n × n)
    uv: torch.Tensor         # (N, 2) coordenadas dos pixels dentro do círculo, em [-1, 1]
    vista: torch.Tensor      # (N, 3) direção em coordenadas da vista (x dir., y cima, z frente)
    omega: torch.Tensor      # (N,) ângulo sólido de cada pixel, sr
    indice_pixel: torch.Tensor  # (N,) posição linear no quadro n × n (para montar a imagem)


def grade_olho_de_peixe(n: int = 96) -> Olhos:
    """Pixels de uma imagem olho-de-peixe angular (r ∝ θ) de 180°."""
    c = (torch.arange(n, dtype=torch.float64) + 0.5) / n * 2 - 1
    v, u = torch.meshgrid(-c, c, indexing="ij")  # v para cima na imagem
    r = torch.sqrt(u**2 + v**2)
    dentro = r <= 1.0
    u, v, r = u[dentro], v[dentro], r[dentro]
    teta = r * (math.pi / 2)
    phi = torch.atan2(v, u)
    s = torch.sin(teta)
    vista = torch.stack([s * torch.cos(phi), s * torch.sin(phi), torch.cos(teta)], dim=1)
    d_teta = math.pi / n
    sinc = torch.where(teta > 1e-9, s / teta.clamp(min=1e-12), torch.ones_like(teta))
    omega = d_teta**2 * sinc
    return Olhos(n, torch.stack([u, v], 1), vista, omega, dentro.flatten().nonzero().flatten())


def base_da_vista(frente: torch.Tensor, cima=(0.0, 0.0, 1.0)):
    """(V, 3) x3: direita, cima, frente (ortonormais)."""
    f = frente / frente.norm(dim=1, keepdim=True)
    c = torch.tensor(cima, dtype=f.dtype).expand_as(f).clone()
    # olhando para cima/baixo, "cima" fica paralelo à frente: usa o eixo y
    paralelo = torch.linalg.cross(f, c).norm(dim=1) < 1e-6
    c[paralelo] = torch.tensor([0.0, 1.0, 0.0], dtype=f.dtype)
    d = torch.linalg.cross(f, c)
    d = d / d.norm(dim=1, keepdim=True)
    return d, torch.linalg.cross(d, f), f


def indice_de_posicao(vista: torch.Tensor) -> torch.Tensor:
    """(N,) índice de posição P (1 = na linha de visão; máx. 16).

    Acima da linha de visão: Guth (Luckiesh & Guth 1949, ajuste usado no evalglare)
      P = exp[(35,2 − 0,31889τ − 1,22e^(−2τ/9))·1e-3·σ + (21 + 0,26667τ − 0,002963τ²)·1e-5·σ²]
      σ = ângulo à linha de visão, τ = ângulo do plano da fonte com a vertical (graus)
    Abaixo: Iwata & Tokura (1997), P = 1 + 0,8·R/D (R/D < 0,6) ou 1 + 1,2·R/D, R/D = tg σ
    """
    x, y, z = vista.unbind(-1)
    sigma = torch.rad2deg(torch.acos(z.clamp(-1, 1)))
    tau = torch.rad2deg(torch.atan2(x.abs(), y))  # 0 = acima, 90 = ao lado
    tau = tau.clamp(0, 90)
    expo = (35.2 - 0.31889 * tau - 1.22 * torch.exp(-2 * tau / 9)) * 1e-3 * sigma + \
           (21 + 0.26667 * tau - 0.002963 * tau**2) * 1e-5 * sigma**2
    p_guth = torch.exp(expo)
    rd = torch.tan(torch.deg2rad(sigma.clamp(max=89.9)))
    p_iwata = torch.where(rd < 0.6, 1 + 0.8 * rd, 1 + 1.2 * rd)
    p = torch.where(y >= 0, p_guth, p_iwata)
    return p.clamp(1.0, 16.0)


# ------------------------------------------------------------------ kernel
@wp.kernel
def _k_coef_pixels(ids: wp.array(dtype=wp.uint64), var: wp.array(dtype=wp.int32),
                   loc: wp.array(dtype=wp.int32), base_face: wp.array(dtype=wp.int32),
                   rho: wp.array(dtype=float), a0_vidro: wp.array(dtype=float),
                   olho: wp.array(dtype=wp.vec3), dir_v: wp.array(dtype=wp.vec3),
                   cima_v: wp.array(dtype=wp.vec3), frente_v: wp.array(dtype=wp.vec3),
                   uv: wp.array(dtype=wp.vec2), n_pix: int, passo_uv: float,
                   amostras: int, max_reflexoes: int, semente: int,
                   alpha: float, n_faixas: int,
                   npf: wp.array(dtype=wp.int32), ini: wp.array(dtype=wp.int32), col_solo: int,
                   c_dir: wp.array2d(dtype=float), c_ref: wp.array2d(dtype=float)):
    linha, k = wp.tid()            # linha = vista * n_pix + pixel
    vi = linha / n_pix
    pi = linha - vi * n_pix
    # semente pela vista local (dentro da variante): independe do lote
    estado = wp.rand_init(semente, (loc[vi] * n_pix + pi) * amostras + k)
    # direção com sorteio dentro do pixel (projeção angular: r ∝ θ)
    u = uv[pi][0] + (wp.randf(estado) - 0.5) * passo_uv
    v = uv[pi][1] + (wp.randf(estado) - 0.5) * passo_uv
    r = wp.min(wp.sqrt(u * u + v * v), 1.0)
    teta = r * 1.5707963267948966
    phi = wp.atan2(v, u)
    st = wp.sin(teta)
    d = wp.normalize(dir_v[vi] * (st * wp.cos(phi)) + cima_v[vi] * (st * wp.sin(phi))
                     + frente_v[vi] * wp.cos(teta))
    malha = ids[var[vi]]
    b0 = base_face[var[vi]]
    o = olho[vi]
    peso = float(1.0)
    refl = int(0)
    contrib = 1.0 / float(amostras)
    passos = int(0)
    while passos < 64:
        passos += 1
        q = wp.mesh_query_ray(malha, o + d * EPS, d, 1.0e6)
        if not q.result:
            col = col_solo
            if d[2] > 0.0:
                col = indice_patch(d, alpha, n_faixas, npf, ini)
            if refl == 0:
                wp.atomic_add(c_dir, linha, col, peso * contrib)
            else:
                wp.atomic_add(c_ref, linha, col, peso * contrib)
            return
        f = b0 + q.face
        p = o + d * (q.t + EPS)
        a0 = a0_vidro[f]
        if a0 > 0.0:
            peso = peso * transmitancia_vidro(wp.dot(d, q.normal), a0)
            o = p
        else:
            if refl >= max_reflexoes:
                return
            peso = peso * rho[f]
            if peso < 1.0e-6:
                return
            nn = q.normal
            if wp.dot(nn, d) > 0.0:
                nn = -nn
            refl += 1
            o = p + nn * EPS
            d = direcao_cosseno(nn, wp.randf(estado), wp.randf(estado))


# ------------------------------------------------------------------ interface
@dataclass
class Vistas:
    olho: torch.Tensor      # (V, 3)
    frente: torch.Tensor    # (V, 3)
    grade: Olhos
    c_dir: torch.Tensor     # (V, N, P + 1) luminância por unidade de luminância do patch
    c_ref: torch.Tensor     # (V, N, P + 1)
    posicao: torch.Tensor   # (V, N) índice de posição de cada pixel
    cos_frente: torch.Tensor  # (N,) cos θ de cada pixel (em relação à frente)
    variante: torch.Tensor | int | None = None  # variante de cada vista (cena em lote)


def coeficientes_vistas(cena: Cena, materiais: Materiais, ceu: Ceu, olho, frente, variante=None,
                        n: int = 96, amostras: int = 32, max_reflexoes: int = 5,
                        semente: int = 7) -> Vistas:
    dev = cena.device
    g = grade_olho_de_peixe(n)
    V, N, P = olho.shape[0], g.uv.shape[0], ceu.n_patches
    var = cena._variantes(variante, V)
    d, c, f = base_da_vista(frente.to(torch.float64))
    a0 = torch.tensor([absorcao_vidro(t) for t in materiais.transmitancia.tolist()], dtype=torch.float32)
    c_dir = torch.zeros(V * N, P + 1, dtype=torch.float32, device=dev)
    c_ref = torch.zeros(V * N, P + 1, dtype=torch.float32, device=dev)
    w = lambda t, dt=None: wp.from_torch(t.to(device=dev).contiguous(), dtype=dt)
    wp.launch(_k_coef_pixels, dim=(V * N, amostras), device=dev, inputs=[
        cena.ids, var, cena._indice_local(variante, V), w(materiais.base_face),
        w(materiais.refletancia.float()), w(a0),
        w(olho.float(), wp.vec3), w(d.float(), wp.vec3), w(c.float(), wp.vec3), w(f.float(), wp.vec3),
        w(g.uv.float(), wp.vec2), N, 2.0 / n, amostras, max_reflexoes, semente,
        float(ceu.alpha), int(ceu.n_por_faixa.shape[0]),
        w(ceu.n_por_faixa.int()), w(ceu.inicio_faixa.int()), P,
        wp.from_torch(c_dir), wp.from_torch(c_ref)])
    pos = indice_de_posicao(g.vista).float().to(dev).expand(V, N).contiguous()
    return Vistas(olho, frente, g, c_dir.reshape(V, N, P + 1), c_ref.reshape(V, N, P + 1),
                  pos, g.vista[:, 2].float().to(dev), variante)


def dgp(ev: torch.Tensor, soma_fontes: torch.Tensor, correcao_baixa_luz: bool = True):
    """DGP a partir de Ev (lux) e Σ Ls²·ω/P² (já somado)."""
    ev_seg = ev.clamp(min=1e-3)
    g = 5.87e-5 * ev + 9.18e-2 * torch.log10(1 + soma_fontes / ev_seg**1.87) + 0.16
    if correcao_baixa_luz:
        x = torch.exp(0.024 * ev - 4)
        g = torch.where(ev < 1000, g * x / (1 + x), g)
    return g.clamp(max=1.0)


def avaliar_imagem(lum: torch.Tensor, vistas: Vistas, limiar: float = 2000.0,
                   sol: tuple | None = None, correcao_baixa_luz: bool = True):
    """lum: (V, N, T) luminância dos pixels.
    sol = (ev_sol, e_tau, p_sol), cada um (V, T): iluminância do sol no olho
    (τ·cos·E_dn), iluminância normal que passa (τ·E_dn) e índice de posição.
    Retorna Ev, DGP (V, T)."""
    om = vistas.grade.omega.to(lum)[None, :, None]
    cosf = vistas.cos_frente.to(lum)[None, :, None]
    ev = (lum * om * cosf).sum(1)
    if limiar < 100:  # fator × luminância média da imagem
        media = (lum * om).sum(1, keepdim=True) / om.sum()
        lim = limiar * media
    else:
        lim = torch.full_like(lum[:, :1], limiar)
    fonte = lum > lim
    soma = (torch.where(fonte, lum, 0) ** 2 * om / vistas.posicao[..., None].to(lum) ** 2).sum(1)
    if sol is not None:
        ev_sol, e_tau, p_sol = (t.to(lum) for t in sol)
        ev = ev + ev_sol
        # sol como fonte do tamanho real: L = τ·E_dn/ω_sol  ->  L²·ω = (τ·E_dn)²/ω_sol
        soma = soma + (e_tau**2 / OMEGA_SOL) / p_sol.clamp(min=1.0) ** 2
    return ev, dgp(ev, soma, correcao_baixa_luz)


def luminancia_pixels(vistas: Vistas, L_dif: torch.Tensor, L_tot: torch.Tensor):
    """(V, N, T) a partir das colunas de céu (P + 1, T) difusa e total (com solo)."""
    dev = vistas.c_dir.device
    return torch.einsum("vnp,pt->vnt", vistas.c_dir, L_dif.to(dev).float()) + \
        torch.einsum("vnp,pt->vnt", vistas.c_ref, L_tot.to(dev).float())


def colunas_ceu(matriz_luz: MatrizCeu, horas, refletancia_solo: float = 0.2):
    """Luminâncias (P + 1, T) difusa e total com a linha do solo, nas horas pedidas."""
    ceu = matriz_luz.ceu
    om, cz = ceu.angulo_solido, ceu.vetores[:, 2]

    def com_solo(L):
        e_h = (L * (om * cz)[:, None]).sum(0, keepdim=True)
        return torch.cat([L, refletancia_solo * e_h / math.pi])

    dif = com_solo(matriz_luz.difusa[:, horas].double())
    tot = com_solo(matriz_luz.total[:, horas].double())
    dif[-1] = tot[-1]  # solo iluminado também pelo sol
    return dif, tot


def sol_nas_vistas(cena: Cena, materiais: Materiais, vistas: Vistas, vetores_sol, e_normal_sol):
    """(ev_sol, e_tau, p_sol), cada um (V, T) — ver `avaliar_imagem`."""
    fator = fator_sol_direto(cena, materiais, vistas.olho, vistas.frente, vetores_sol,
                             variante=vistas.variante)  # τ·cos
    d, c, f = base_da_vista(vistas.frente.to(torch.float64))
    vs = vetores_sol.to(torch.float64)
    na_vista = torch.stack([vs @ d.T, vs @ c.T, vs @ f.T], dim=-1).transpose(0, 1)  # (V, T, 3)
    p = indice_de_posicao(na_vista.reshape(-1, 3)).reshape(na_vista.shape[:2]).to(fator)
    cos = na_vista[..., 2].to(fator)
    e_n = e_normal_sol.to(fator)[None, :]
    ev_sol = fator * e_n
    e_tau = torch.where(cos > 1e-6, fator / cos.clamp(min=1e-6), torch.zeros_like(fator)) * e_n
    return ev_sol, e_tau, p


def dgp_anual(cena: Cena, materiais: Materiais, vistas: Vistas, matriz_luz: MatrizCeu,
              vetores_sol, e_normal_sol, horas, limiar: float = 2000.0,
              lote_horas: int = 256, correcao_baixa_luz: bool = True):
    """(V, T) DGP nas horas pedidas (índices), em lotes para caber na memória."""
    horas = torch.as_tensor(horas)
    saida_ev, saida_dgp = [], []
    for i in range(0, horas.shape[0], lote_horas):
        h = horas[i: i + lote_horas]
        dif, tot = colunas_ceu(matriz_luz, h)
        lum = luminancia_pixels(vistas, dif, tot)
        sol = sol_nas_vistas(cena, materiais, vistas, vetores_sol[h], e_normal_sol[h])
        ev, g = avaliar_imagem(lum, vistas, limiar, sol, correcao_baixa_luz)
        saida_ev.append(ev)
        saida_dgp.append(g)
    return torch.cat(saida_ev, 1), torch.cat(saida_dgp, 1)


def imagem(vistas: Vistas, lum_vn: torch.Tensor, vista: int = 0) -> torch.Tensor:
    """(n, n) imagem de luminância de uma vista (fora do círculo = 0)."""
    n = vistas.grade.n
    img = torch.zeros(n * n, dtype=lum_vn.dtype, device=lum_vn.device)
    img[vistas.grade.indice_pixel.to(lum_vn.device)] = lum_vn[vista]
    return img.reshape(n, n)


def autonomia_ofuscamento(g: torch.Tensor, limite: float = 0.4) -> torch.Tensor:
    """GA (glare autonomy): % das horas com DGP < limite, por vista."""
    return (g < limite).float().mean(1) * 100

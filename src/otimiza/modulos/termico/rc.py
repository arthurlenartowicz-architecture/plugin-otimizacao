"""Modelo térmico RC horário em lote, multicamadas (no espírito da ISO 52016-1).

Uma zona por variante. Cada tipo de elemento (parede, cobertura, piso no solo, piso
interno/adiabático, vidro) é uma construção em camadas, dividida em nós de ≤ 4 cm, com
nós de superfície externa e interna (sem massa). Nós extras: ar (capacidade do ar) e
temperatura radiante média (estrela radiante: cada superfície interna troca h_r·A com ele).
  externo  h_e = 4 + 4·v_local + h_re (vento médio do ano na altura do elemento, perfil do
           EnergyPlus para o terreno), sol absorvido α·I e onda longa com o céu
           F_r·h_r·(θ_céu − θ_e) (F_r = F_céu·β, como o EnergyPlus; h_r hora a hora na
           média entre céu e ar)
  interno  convecção para o ar (parede e piso 2,0; teto 1,5 W/m²K, típicos do TARP do
           EnergyPlus) e radiação ~5,2·A
  vidro    um nó: 1/(1/U − 1/h_int) para fora, convecção + radiação para dentro
  solo     face externa do piso na temperatura do solo (mensal, cabeçalho do EPW)
  ar       infiltração ρ·c·vazão (ρ médio do ano pela T e pressão do EPW)
Ganhos: convectivos no ar; radiantes no nó radiante. Sol pelos vidros (FS × I com o fator
angular): a parte transmitida (τ = 0,84·FS) direta cai no piso (como o EnergyPlus), que
absorve 80% (o resto e o difuso vão às faces internas por área × absortância); a parte absorvida no vidro (FS − τ)
entra pela face interna da janela. Sem piso, tudo no nó radiante. Climatização ideal no ar (como o IdealLoads do EnergyPlus), sem limite: como o
sistema é linear, a potência que leva o ar ao termostato sai de uma coluna da inversa.

Euler implícito com passo de 1 h: (C/Δt + K)·x(t+1) = C/Δt·x(t) + B·u(t+1). A matriz é a
mesma o ano todo; a inversa é calculada uma vez por variante e o laço das horas roda num
kernel do Warp (uma thread por variante). O ano roda duas vezes (a 1ª é o aquecimento).
"""
from __future__ import annotations

import datetime
import math
from dataclasses import dataclass, field

import torch
import warp as wp

SIGMA_EP = 5.6697e-8
CP_AR = 1006.0               # J/kgK
H_RI = 4 * 0.9 * SIGMA_EP * 295.15 ** 3   # radiação interna, W/m²K (~5,2)
# convecção interna: valores típicos do TARP do EnergyPlus (natural, ΔT superfície–ar de 1–3 K)
H_CI = {"parede": 2.0, "vidro": 2.0, "cobertura": 1.5, "piso_solo": 2.0, "piso_interno": 2.0}
# fração efetiva de céu na onda longa (F_céu · β, β = √(½(1 + cos incl.))), como o EnergyPlus
F_R = {"parede": 0.5 * math.sqrt(0.5), "vidro": 0.5 * math.sqrt(0.5), "cobertura": 1.0}
DX_MAX = 0.04                # m, espessura máxima por nó

# construções genéricas do Honeybee (camadas de fora para dentro: espessura m, λ W/mK,
# ρ kg/m³, c J/kgK); α = absortância solar da face externa
CONSTRUCOES = {
    "parede": dict(camadas=[(0.1, 0.9, 1920, 790), (0.1, 0.53, 1280, 840), (0.05, 0.03, 43, 1210),
                            (0.1, 0.667, 1.28, 1000), (0.0127, 0.16, 800, 1090)], alfa=0.65),
    "cobertura": dict(camadas=[(0.01, 0.16, 1120, 1460), (0.05, 0.03, 43, 1210), (0.1, 0.53, 1280, 840),
                               (0.1, 0.556, 1.28, 1000), (0.02, 0.06, 368, 590)], alfa=0.65),
    "piso_solo": dict(camadas=[(0.05, 0.03, 43, 1210), (0.2, 1.95, 2240, 900)], alfa=0.0),
    "piso_interno": dict(camadas=[(0.02, 0.06, 368, 590), (0.1, 0.556, 1.28, 1000), (0.1, 0.53, 1280, 840)],
                         alfa=0.0),
}
U_VIDRO = 1.688   # Generic Double Pane (com películas)
ELEMENTOS = ("parede", "cobertura", "piso_solo", "piso_interno", "vidro")
EXTERNO = {"parede": "ar", "cobertura": "ar", "piso_solo": "solo", "piso_interno": "adiabatico",
           "vidro": "ar"}
TAU_FS = 0.3553 / 0.4241   # τ_sol/FS do Generic Double Pane: o resto do FS é absorvido no vidro
ALFA_PISO = 0.8            # absortância solar da face interna do piso (concreto)
# absortância solar das faces internas (Honeybee genérico: gesso 0,5, forro acústico 0,2,
# concreto 0,8): o sol difuso que entra é repartido por área × absortância (como o EnergyPlus)
ALFA_INT = {"parede": 0.5, "cobertura": 0.2, "piso_solo": 0.8, "piso_interno": 0.8, "vidro": 0.05}
# entradas horárias u: 0 θ_e, 1 θ_solo, 2 h_r·(θ_céu − θ_e) em W/m², 3 α·I parede, 4 α·I cobertura,
# 5 sol direto absorvido no piso, 6 sol radiante (difuso + refletido no piso), 7 sol absorvido
# no vidro (entra pela face interna), 8 radiante interno, 9 convectivo interno
N_U = 10
N_COM = 3
vec_u = wp.types.vector(length=N_U, dtype=wp.float64)


@dataclass
class Programa:
    """Cargas internas e termostato (padrão = o caso de validação contra o EnergyPlus)."""
    pessoas_m2: float = 0.0565
    w_pessoa: float = 120.0          # sensível
    rad_pessoas: float = 0.3
    iluminacao_w_m2: float = 10.0
    rad_iluminacao: float = 0.57     # radiante + visível
    equipamentos_w_m2: float = 10.0
    rad_equipamentos: float = 0.5
    fora_do_horario: float = 0.05    # iluminação/equipamentos fora do horário
    aquecimento: float = 20.0        # °C, o tempo todo
    resfriamento: float = 24.0
    ach: float = 0.5                 # infiltração, trocas/h
    cop_aquecimento: float = 3.0
    cop_resfriamento: float = 3.0
    inicio: int = 8                  # ocupação seg–sex das 8 às 18 h
    fim: int = 18
    terreno: str = "cidade"          # perfil de vento (como o EnergyPlus): campo, suburbio, cidade


# perfil de vento do EnergyPlus: (expoente, camada limite em m); estação meteorológica = campo
TERRENOS = {"campo": (0.14, 270.0), "suburbio": (0.22, 370.0), "cidade": (0.33, 460.0)}


def vento_local(v10: torch.Tensor, altura: torch.Tensor, terreno: str = "cidade") -> torch.Tensor:
    """(V, H) vento na altura (V,) a partir do vento a 10 m do EPW (fórmula do EnergyPlus)."""
    a_met, d_met = TERRENOS["campo"]
    a, d = TERRENOS[terreno]
    z = altura.clamp(min=0.5)[:, None]
    return v10[None, :] * (d_met / 10.0) ** a_met * (z / d) ** a


def ocupacao(programa: Programa, ano: int = 2017) -> torch.Tensor:
    """(8760,) 1 nas horas ocupadas (hora do EPW h = intervalo h−1..h)."""
    t0 = datetime.datetime(ano, 1, 1)
    v = []
    for i in range(8760):
        t = t0 + datetime.timedelta(hours=i)
        v.append(1.0 if t.weekday() < 5 and programa.inicio <= t.hour < programa.fim else 0.0)
    return torch.tensor(v, dtype=torch.float64)


def temperatura_solo_horaria(clima) -> torch.Tensor:
    """(H,) θ_solo do mês de cada hora (cabeçalho do EPW); sem cabeçalho, média anual do ar."""
    if clima.temp_solo is not None:
        return clima.temp_solo[clima.mes - 1]
    return clima["temp_bulbo_seco"].mean().expand(clima["temp_bulbo_seco"].shape[0]).clone()


def u_elemento(nome: str, h_e: float = 25.0) -> float:
    """U (W/m²K) da construção com películas (interna: convecção + radiação)."""
    if nome == "vidro":
        return U_VIDRO
    r = sum(d / k for d, k, _, _ in CONSTRUCOES[nome]["camadas"]) + 1 / (H_CI[nome] + H_RI)
    if EXTERNO[nome] == "ar":
        r += 1 / h_e
    return 1 / r


# ---------------------------------------------------------------- rede de nós
@dataclass
class Rede:
    """Rede "por m²" de cada elemento + nós comuns (ar = 0, radiante = 1)."""
    n: int
    ar: int
    mrt: int
    k_el: dict          # elemento → (N, N) condutâncias por m² (sem a ligação ao exterior)
    c_el: dict          # elemento → (N,) capacidade por m² (J/K)
    ext: dict           # elemento → nó ligado ao exterior (ar externo ou solo); None = adiabático
    sup_int: dict       # elemento → nó de superfície interna
    g_solo: float = 0.0  # condutância por m² do solo ao 1º nó do piso


def montar_rede() -> Rede:
    ar, mrt = 0, 1
    nos = 2
    lig, cap, ext, sup = {}, {}, {}, {}
    g_solo = 0.0
    for el in ELEMENTOS:
        ligs, caps = [], {}
        if el == "vidro":
            w = nos
            nos += 1
            ext[el], sup[el] = w, w
            lig[el] = [(w, ar, H_CI[el]), (w, mrt, H_RI)]
            cap[el] = caps
            continue
        ultimo, r_acum = None, 0.0   # último nó e resistência acumulada desde ele
        if EXTERNO[el] == "ar":
            ultimo = nos
            ext[el] = nos
            nos += 1
        else:
            ext[el] = None
        for d, lam, rho, c in CONSTRUCOES[el]["camadas"]:
            if rho * c * d < 1e3:           # câmara de ar: só resistência
                r_acum += d / lam
                continue
            n_sub = max(1, math.ceil(d / DX_MAX - 1e-9))
            dx = d / n_sub
            for _ in range(n_sub):
                i = nos
                nos += 1
                caps[i] = rho * c * dx
                r_acum += dx / (2 * lam)
                if ultimo is not None:
                    ligs.append((ultimo, i, 1 / r_acum))
                elif EXTERNO[el] == "solo":
                    ext[el] = i
                    g_solo = 1 / r_acum
                ultimo, r_acum = i, dx / (2 * lam)
        si = nos
        nos += 1
        ligs += [(ultimo, si, 1 / r_acum), (si, ar, H_CI[el]), (si, mrt, H_RI)]
        sup[el] = si
        lig[el], cap[el] = ligs, caps

    k_el, c_el = {}, {}
    for el in ELEMENTOS:
        k = torch.zeros(nos, nos, dtype=torch.float64)
        c = torch.zeros(nos, dtype=torch.float64)
        for a, b, gg in lig[el]:
            k[a, a] += gg
            k[b, b] += gg
            k[a, b] -= gg
            k[b, a] -= gg
        for i, val in cap[el].items():
            c[i] = val
        if el == "piso_solo":
            k[ext[el], ext[el]] += g_solo
        k_el[el], c_el[el] = k, c
    return Rede(nos, ar, mrt, k_el, c_el, ext, sup, g_solo)


REDE = montar_rede()


@dataclass
class Zonas:
    """Sistema de V zonas em coordenadas modais (pronto para simular).

    Euler implícito M·x(t+1) = D·x(t) + B·u(t+1) + e_ar·P(t+1), D = C/Δt. Só os nós com massa
    têm memória; em y = √D·x_m a matriz de transição é simétrica, S = √D·(M⁻¹)_mm·√D = Q·Λ·Qᵀ,
    e cada modo z = Qᵀ·y evolui sozinho: z(t+1) = Λ·z(t) + Gm·u(t+1) + cm·P(t+1). As saídas
    (ar e operativa) são y(t+1) = h·z(t) + g·u(t+1) + k·P(t+1). Exato (sem truncar modos)."""
    lam: torch.Tensor      # (V, nm)
    gm: torch.Tensor       # (V, nm, K)
    cm: torch.Tensor       # (V, nm)
    h: torch.Tensor        # (V, 2, nm) linhas de saída: 0 ar, 1 operativa
    gout: torch.Tensor     # (V, 2, K)
    kout: torch.Tensor     # (V, 2)
    z0: torch.Tensor       # (V, nm) estado inicial (tudo a 20 °C)
    u_var: torch.Tensor    # (V, 7, H) entradas por variante (u[3..9])
    u_com: torch.Tensor    # (3, H) entradas comuns (u[0..2])
    area_piso: torch.Tensor
    info: dict = field(default_factory=dict)


def montar_zonas(geo: dict, clima, programa: Programa, irr: dict, fator_luz=None,
                 device="cpu") -> Zonas:
    """geo: (V,) parede (opaca), vidro, cobertura, solo, piso_interno, area_piso, volume, fs,
    altura_parede, altura_cobertura (m², m³, m). irr: (V, H) W (Σ área·irradiância) em parede,
    cobertura, vidro (este já com o fator angular). fator_luz: (V,) fração da iluminação
    desligada pela luz natural nas horas ocupadas (0 = sem controle)."""
    f64 = dict(dtype=torch.float64, device=device)
    r = REDE
    g = {k: torch.as_tensor(v).to(**f64) for k, v in geo.items()}
    nv = g["volume"].shape[0]
    te = clima["temp_bulbo_seco"].to(**f64)
    v10 = clima["vento_velocidade"].to(**f64)
    area = {"parede": g["parede"], "cobertura": g["cobertura"], "piso_solo": g["solo"],
            "piso_interno": g["piso_interno"], "vidro": g["vidro"]}
    area = {k: v.clamp(min=1e-4) for k, v in area.items()}   # elemento ausente: 1 cm², inerte

    h_re = 4 * 0.9 * SIGMA_EP * (te.mean() + 273.15) ** 3
    h_e = {k: 4 + 4 * vento_local(v10, g["altura_" + ("cobertura" if k == "cobertura" else "parede")],
                                  programa.terreno).mean(1) + h_re
           for k in ("parede", "cobertura", "vidro")}

    K = torch.zeros(nv, r.n, r.n, **f64)
    C = torch.zeros(nv, r.n, **f64)
    B = torch.zeros(nv, r.n, N_U, **f64)
    for el in ELEMENTOS:
        K += area[el][:, None, None] * r.k_el[el].to(**f64)
        C += area[el][:, None] * r.c_el[el].to(**f64)
        i = r.ext[el]
        if EXTERNO[el] == "ar":
            if el == "vidro":   # U do vidro já inclui as películas: o nó fica na face interna
                ge = torch.full_like(h_e[el], 1 / (1 / U_VIDRO - 1 / (H_CI["vidro"] + H_RI)))
            else:
                ge = h_e[el]
            K[:, i, i] += area[el] * ge
            B[:, i, 0] += area[el] * ge
            B[:, i, 2] += area[el] * F_R[el] * (ge / h_e[el])   # onda longa com o céu (u2 em W/m²)
        elif EXTERNO[el] == "solo":
            B[:, i, 1] += area[el] * r.g_solo
    B[:, r.ext["parede"], 3] = 1.0
    B[:, r.ext["cobertura"], 4] = 1.0
    # sol direto: na face interna dos pisos (pela área); sem piso, no nó radiante
    a_piso = g["solo"] + g["piso_interno"]
    tem = a_piso > 0
    f_solo = torch.where(tem, g["solo"] / a_piso.clamp(min=1e-9), torch.zeros_like(a_piso))
    B[:, r.sup_int["piso_solo"], 5] = f_solo
    B[:, r.sup_int["piso_interno"], 5] = torch.where(tem, 1 - f_solo, torch.zeros_like(a_piso))
    B[:, r.mrt, 5] = (~tem).double()
    soma_aa = sum(area[el] * ALFA_INT[el] for el in ELEMENTOS)
    for el in ELEMENTOS:
        B[:, r.sup_int[el], 6] = area[el] * ALFA_INT[el] / soma_aa
    B[:, r.sup_int["vidro"], 7] = 1.0
    B[:, r.mrt, 8] = 1.0
    B[:, r.ar, 9] = 1.0

    # ar: capacidade e infiltração (ρ médio do ano)
    rho = (clima["pressao"].to(**f64) / (287.05 * (te + 273.15))).mean()
    h_ve = programa.ach * g["volume"] / 3600.0 * rho * CP_AR
    C[:, r.ar] += rho * CP_AR * g["volume"]
    K[:, r.ar, r.ar] += h_ve
    B[:, r.ar, 0] += h_ve

    dt = 3600.0
    minv = torch.linalg.inv(K + torch.diag_embed(C / dt))
    w = torch.zeros(nv, r.n, **f64)    # operativa = ½ ar + ½ média (pela área) das superfícies
    a_tot = sum(area[el] for el in ELEMENTOS)
    for el in ELEMENTOS:
        w[:, r.sup_int[el]] += 0.5 * area[el] / a_tot
    w[:, r.ar] += 0.5
    m = (C[0] > 0).nonzero().flatten()           # nós com massa (os mesmos em todas)
    sq = (C[:, m] / dt).sqrt()                    # (V, nm)
    s_mat = sq[:, :, None] * minv[:, m][:, :, m] * sq[:, None, :]
    lam, q = torch.linalg.eigh(0.5 * (s_mat + s_mat.transpose(1, 2)))
    mb = minv @ B                                 # (V, N, K)
    qt = q.transpose(1, 2)
    gm = qt @ (sq[:, :, None] * mb[:, m])
    cm = (qt @ (sq * minv[:, m, r.ar])[:, :, None])[:, :, 0]
    saida = torch.stack([minv[:, r.ar], (w[:, None, :] @ minv)[:, 0]], dim=1)   # (V, 2, N)
    h = (saida[:, :, m] * sq[:, None, :]) @ q
    gout = saida @ B
    kout = saida[:, :, r.ar]
    z0 = (qt @ (sq * 20.0)[:, :, None])[:, :, 0]

    # entradas horárias
    occ = ocupacao(programa).to(**f64)
    sched = torch.maximum(occ, torch.full_like(occ, programa.fora_do_horario))
    af = g["area_piso"][:, None]
    luz = programa.iluminacao_w_m2 * af * sched[None, :]
    if fator_luz is not None:
        fl = torch.nan_to_num(torch.as_tensor(fator_luz).to(**f64), nan=0.0).clamp(0, 1)
        luz = luz * (1 - fl[:, None] * occ[None, :])
    pes = programa.pessoas_m2 * programa.w_pessoa * af * occ[None, :]
    eq = programa.equipamentos_w_m2 * af * sched[None, :]
    conv = (1 - programa.rad_pessoas) * pes + (1 - programa.rad_iluminacao) * luz + \
        (1 - programa.rad_equipamentos) * eq
    rad = programa.rad_pessoas * pes + programa.rad_iluminacao * luz + programa.rad_equipamentos * eq
    from otimiza.modulos.termico.conforto_externo import temperatura_ceu
    t_ceu = temperatura_ceu(clima["rad_infravermelha_horiz"].to(**f64), 1.0)
    fs = g["fs"][:, None]
    sol_vidro = fs * irr["vidro"].to(**f64)
    dir_t = TAU_FS * fs * irr.get("vidro_direta", torch.zeros_like(irr["vidro"])).to(**f64)
    tot_t = TAU_FS * sol_vidro
    no_piso = ALFA_PISO * dir_t * tem.double()[:, None]
    u_var = torch.stack([CONSTRUCOES["parede"]["alfa"] * irr["parede"].to(**f64),
                         CONSTRUCOES["cobertura"]["alfa"] * irr["cobertura"].to(**f64),
                         no_piso, tot_t - no_piso, sol_vidro - tot_t, rad, conv], dim=1)
    # troca com o céu linearizada hora a hora entre o céu e a superfície (~ar externo)
    h_ceu = 4 * 0.9 * SIGMA_EP * ((te + t_ceu) / 2 + 273.15) ** 3
    u_com = torch.stack([te, temperatura_solo_horaria(clima).to(**f64), h_ceu * (t_ceu - te)])
    return Zonas(lam, gm, cm, h, gout, kout, z0, u_var, u_com, g["area_piso"],
                 info={"C_total_kJ_K": C.sum(1) / 1000, "h_ve": h_ve, "luz_w": luz, "equip_w": eq,
                       "solar_w": sol_vidro, "u": {el: u_elemento(el) for el in ELEMENTOS}})


@wp.kernel
def _k_rc(lam: wp.array2d(dtype=wp.float64), gm: wp.array3d(dtype=wp.float64),
          cm: wp.array2d(dtype=wp.float64), h: wp.array3d(dtype=wp.float64),
          gout: wp.array3d(dtype=wp.float64), kout: wp.array2d(dtype=wp.float64),
          z: wp.array2d(dtype=wp.float64), u_var: wp.array3d(dtype=wp.float64),
          u_com: wp.array2d(dtype=wp.float64), t_aq: wp.array(dtype=wp.float64),
          t_rf: wp.array(dtype=wp.float64), climatizar: int, passadas: int,
          phi_hc: wp.array2d(dtype=wp.float64), t_ar: wp.array2d(dtype=wp.float64),
          t_op: wp.array2d(dtype=wp.float64)):
    # layout com a variante por último (threads vizinhas leem endereços vizinhos)
    v = wp.tid()
    nm = lam.shape[0]
    n_h = u_com.shape[1]
    for ps in range(passadas):
        for hh in range(n_h):
            u = vec_u()
            for c in range(N_COM):
                u[c] = u_com[c, hh]
            for c in range(N_U - N_COM):
                u[N_COM + c] = u_var[c, hh, v]
            ya = wp.float64(0.0)
            yo = wp.float64(0.0)
            for c in range(N_U):
                ya += gout[0, c, v] * u[c]
                yo += gout[1, c, v] * u[c]
            for k in range(nm):
                zk = z[k, v]
                ya += h[0, k, v] * zk
                yo += h[1, k, v] * zk
            pot = wp.float64(0.0)
            if climatizar != 0:
                if ya < t_aq[hh]:
                    pot = (t_aq[hh] - ya) / kout[0, v]
                if ya > t_rf[hh]:
                    pot = (t_rf[hh] - ya) / kout[0, v]
            for k in range(nm):
                acc = lam[k, v] * z[k, v] + cm[k, v] * pot
                for c in range(N_U):
                    acc += gm[k, c, v] * u[c]
                z[k, v] = acc
            if ps == passadas - 1:
                phi_hc[hh, v] = pot
                t_ar[hh, v] = ya + kout[0, v] * pot
                t_op[hh, v] = yo + kout[1, v] * pot


def simular(z: Zonas, t_aq: torch.Tensor, t_rf: torch.Tensor, climatizar: bool = True,
            passadas: int = 2) -> dict[str, torch.Tensor]:
    """Roda o ano. Saída (V, H): phi_hc (W, + aquecimento, − resfriamento), t_ar, t_op."""
    dev = z.lam.device
    nv = z.lam.shape[0]
    h = z.u_com.shape[1]
    f64 = dict(dtype=torch.float64, device=dev)

    def a(t):  # variante para o último eixo
        t = t.to(**f64)
        return wp.from_torch(t.permute(*range(1, t.dim()), 0).contiguous(), dtype=wp.float64)

    def c(t):
        return wp.from_torch(t.to(**f64).contiguous(), dtype=wp.float64)

    saida = {k: torch.empty(h, nv, **f64) for k in ("phi_hc", "t_ar", "t_op")}
    estado = z.z0.to(**f64).t().contiguous()
    wp.launch(_k_rc, dim=nv, device=str(dev) if dev.type == "cuda" else "cpu", inputs=[
        a(z.lam), a(z.gm), a(z.cm), a(z.h), a(z.gout), a(z.kout),
        wp.from_torch(estado, dtype=wp.float64), a(z.u_var),
        c(z.u_com), c(t_aq.expand(h) if t_aq.dim() == 0 else t_aq),
        c(t_rf.expand(h) if t_rf.dim() == 0 else t_rf), int(climatizar), int(passadas),
        wp.from_torch(saida["phi_hc"]), wp.from_torch(saida["t_ar"]), wp.from_torch(saida["t_op"])])
    return {k: v.t() for k, v in saida.items()}

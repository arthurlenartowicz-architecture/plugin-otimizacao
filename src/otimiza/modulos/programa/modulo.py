"""Módulo programa (Etapa 4e): índices urbanísticos e programa de necessidades.

Só geometria (sem clima nem raios). Entradas além da malha: o programa (lote, áreas-meta,
adjacências) em `cena.opcoes["programa"]` (ver necessidades.py) e os ambientes dos pisos
(`Variante.ambientes` / `usos`).

- Projeto = faces fachada, vidro, cobertura, piso, interno (contexto e entorno não contam).
- area_construida = Σ área dos pisos (a mesma área de piso do termico).
- taxa_ocupacao: projeção horizontal do projeto contada numa grade (centro de cada célula dentro
  de algum triângulo projetado; célula 0,5 / 0,25 / 0,1 m nos níveis rápido / padrão / validação).
  Paredes verticais não têm área projetada: a projeção vem de pisos e coberturas.
- altura: z máximo do projeto − cota do lote (z dos pontos do lote; sem z, a base do projeto).
- recuo_min: menor distância horizontal entre o projeto e a divisa; negativa se o projeto sai do
  lote (−a maior distância para fora).
- Ambientes: área de cada uso = Σ áreas dos pisos com aquele nome. Dois ambientes são adjacentes
  quando bordas dos pisos, paralelas, a no máx. `parede_max` uma da outra (a parede entre eles) e
  na mesma cota (±0,5 m), se sobrepõem por pelo menos `comprimento_min` (dá para pôr uma porta).
  Distância entre usos = a menor entre centros (ponderados pela área) dos seus ambientes.
"""
from __future__ import annotations

import math
from collections import defaultdict

import torch

from otimiza.modulos.programa.necessidades import programa_de
from otimiza.nucleo.cena import CODIGO
from otimiza.nucleo.contrato import Metrica, Modulo, registrar
from otimiza.nucleo.geometria import normais_e_areas

PROJETO = ("fachada", "vidro", "cobertura", "piso", "interno")
PASSO = {"rapido": 0.5, "padrao": 0.25, "validacao": 0.1}
COM_LOTE = {"ca", "taxa_ocupacao", "recuo_min"}
COM_AREAS = {"atendimento_programa", "desvio_programa"}
COM_ADJ = {"adjacencia", "distancia_ponderada"}
_SEN_PARALELO = math.sin(math.radians(5))
_DZ_MESMO_PISO = 0.5


# ---------------------------------------------------------------- geometria 2D
def _ccw(P: torch.Tensor) -> torch.Tensor:
    """Triângulos (F, 3, 2) no sentido anti-horário, sem os degenerados (paredes verticais)."""
    a, b, c = P[:, 0], P[:, 1], P[:, 2]
    d2 = (b - a)[:, 0] * (c - a)[:, 1] - (b - a)[:, 1] * (c - a)[:, 0]
    escala = (P.reshape(-1, 2).max(0).values - P.reshape(-1, 2).min(0).values).max().clamp(min=1.0)
    ok = d2.abs() > 1e-10 * escala ** 2
    P, d2 = P[ok], d2[ok]
    neg = d2 < 0
    P = P.clone()
    P[neg] = P[neg][:, [0, 2, 1]]
    return P


def _dentro_de_triangulos(p: torch.Tensor, P: torch.Tensor, eps: float = 1e-9) -> torch.Tensor:
    """(N,) ponto dentro (ou na borda) de algum triângulo anti-horário (F, 3, 2)."""
    if P.shape[0] == 0:
        return torch.zeros(p.shape[0], dtype=torch.bool, device=p.device)
    dentro = torch.ones(p.shape[0], P.shape[0], dtype=torch.bool, device=p.device)
    for i in range(3):
        a, b = P[:, i], P[:, (i + 1) % 3]
        ab = b - a
        ap = p[:, None, :] - a[None]
        dentro &= (ab[None, :, 0] * ap[..., 1] - ab[None, :, 1] * ap[..., 0]) >= -eps
    return dentro.any(1)


def area_projecao(xy: torch.Tensor, tri: torch.Tensor, passo: float) -> float:
    """Área (m²) da projeção horizontal dos triângulos, contada numa grade de células `passo`."""
    if tri.numel() == 0:
        return 0.0
    P = _ccw(xy[tri])
    if P.shape[0] == 0:
        return 0.0
    lo = P.reshape(-1, 2).min(0).values
    hi = P.reshape(-1, 2).max(0).values
    nx, ny = (max(1, math.ceil(float(x) / passo - 1e-9)) for x in (hi - lo))
    xs = lo[0] + (torch.arange(nx, device=xy.device, dtype=xy.dtype) + 0.5) * passo
    por_vez = max(1, int(2e7 // max(1, nx * P.shape[0])))      # linhas da grade por bloco
    n = 0
    for j0 in range(0, ny, por_vez):
        ys = lo[1] + (torch.arange(j0, min(ny, j0 + por_vez), device=xy.device, dtype=xy.dtype) + 0.5) * passo
        g = torch.stack(torch.meshgrid(xs, ys, indexing="ij"), -1).reshape(-1, 2)
        n += int(_dentro_de_triangulos(g, P).sum())
    return n * passo * passo


def _dist_pontos_segmentos(p: torch.Tensor, a: torch.Tensor, b: torch.Tensor) -> torch.Tensor:
    """(N, S) distância de cada ponto a cada segmento a→b."""
    ab = b - a
    t = (((p[:, None] - a[None]) * ab[None]).sum(-1) / (ab * ab).sum(-1).clamp(min=1e-30)[None]).clamp(0, 1)
    return (p[:, None] - (a[None] + t[..., None] * ab[None])).norm(dim=-1)


def ponto_no_poligono(p: torch.Tensor, poli: torch.Tensor) -> torch.Tensor:
    """(N,) regra par-ímpar (cruzamentos de uma semirreta para +x)."""
    x, y = p[:, 0:1], p[:, 1:2]
    x0, y0 = poli[:, 0][None], poli[:, 1][None]
    x1, y1 = poli.roll(-1, 0)[:, 0][None], poli.roll(-1, 0)[:, 1][None]
    corta = (y0 > y) != (y1 > y)
    dy = torch.where((y1 - y0).abs() < 1e-30, torch.full_like(y1, 1e-30), y1 - y0)
    xint = x0 + (y - y0) * (x1 - x0) / dy
    return ((corta & (x < xint)).sum(1) % 2) == 1


def recuo_minimo(xy: torch.Tensor, tri: torch.Tensor, lote: torch.Tensor) -> float:
    """Menor distância (m) do projeto à divisa; negativa se o projeto sai do lote."""
    usados = torch.unique(tri.flatten())
    pv = xy[usados]
    la, lb = lote, lote.roll(-1, 0)
    d = _dist_pontos_segmentos(pv, la, lb).min(1).values
    dentro = ponto_no_poligono(pv, lote)
    if (~dentro).any():
        return -float(d[~dentro].max())
    e0 = torch.cat([tri[:, 0], tri[:, 1], tri[:, 2]])
    e1 = torch.cat([tri[:, 1], tri[:, 2], tri[:, 0]])
    d2 = _dist_pontos_segmentos(lote, xy[e0], xy[e1]).min(1).values     # cantos do lote
    P = _ccw(xy[tri])
    invade = _dentro_de_triangulos(lote, P)     # canto do lote "entrando" no projeto
    if invade.any():
        return -float(d2[invade].min())
    return float(min(d.min(), d2.min()))


# ---------------------------------------------------------------- ambientes
def ambientes_de(v) -> list[dict]:
    """Um dict por ambiente: uso, area, centro (3,), bordas (E, 2, 3) dos pisos."""
    if v.ambientes is None or not v.usos:
        return []
    vert = v.vertices.double().cpu()
    amb = v.ambientes.cpu()
    faces = v.faces.cpu()
    soldado = torch.unique(torch.round(vert * 1000).long(), dim=0, return_inverse=True)[1]  # 1 mm
    saida = []
    for k, uso in enumerate(v.usos):
        f = faces[amb == k]
        if f.numel() == 0:
            continue
        _, a = normais_e_areas(vert, f)
        centro = (vert[f].mean(1) * a[:, None]).sum(0) / a.sum().clamp(min=1e-30)
        eo = torch.cat([f[:, [0, 1]], f[:, [1, 2]], f[:, [2, 0]]])
        chave = soldado[eo].sort(1).values
        _, inv, cont = torch.unique(chave, dim=0, return_inverse=True, return_counts=True)
        borda = eo[cont[inv] == 1]
        saida.append({"uso": uso, "area": float(a.sum()), "centro": centro, "bordas": vert[borda]})
    return saida


def parede_comum(a: torch.Tensor, b: torch.Tensor, parede_max: float) -> float:
    """Comprimento (m) em que as bordas a (Ea, 2, 3) e b (Eb, 2, 3) correm lado a lado."""
    if a.numel() == 0 or b.numel() == 0:
        return 0.0
    pa0, pa1 = a[:, 0, :2], a[:, 1, :2]
    la = (pa1 - pa0).norm(dim=1).clamp(min=1e-12)
    ta = (pa1 - pa0) / la[:, None]
    pb0, pb1 = b[:, 0, :2], b[:, 1, :2]
    tb = (pb1 - pb0) / (pb1 - pb0).norm(dim=1).clamp(min=1e-12)[:, None]

    def cruz(u, w):
        return u[..., 0] * w[..., 1] - u[..., 1] * w[..., 0]

    paralelo = cruz(ta[:, None], tb[None]).abs() < _SEN_PARALELO
    meio = (pb0 + pb1) / 2
    perto = cruz(ta[:, None], meio[None] - pa0[:, None]).abs() <= parede_max + 1e-9
    za, zb = a[..., 2].mean(1), b[..., 2].mean(1)
    mesmo_piso = (za[:, None] - zb[None]).abs() <= _DZ_MESMO_PISO
    s0 = ((pb0[None] - pa0[:, None]) * ta[:, None]).sum(-1)
    s1 = ((pb1[None] - pa0[:, None]) * ta[:, None]).sum(-1)
    sobre = (torch.minimum(la[:, None], torch.maximum(s0, s1))
             - torch.clamp(torch.minimum(s0, s1), min=0)).clamp(min=0)
    return float((sobre * (paralelo & perto & mesmo_piso)).sum())


def metricas_programa(v, prog: dict, area_construida: float) -> dict[str, float]:
    ambs = ambientes_de(v)
    area_uso = defaultdict(float)
    for a in ambs:
        area_uso[a["uso"]] += a["area"]
    metas = prog["areas"]
    r = {}
    if metas:
        total = sum(metas.values())
        r["atendimento_programa"] = 100 * sum(min(area_uso.get(u, 0.0), m) for u, m in metas.items()) / total
        r["desvio_programa"] = 100 * sum(abs(area_uso.get(u, 0.0) - m) for u, m in metas.items()) / total
    util = sum(a["area"] for a in ambs if not metas or a["uso"] in metas)
    r["eficiencia_planta"] = 100 * util / area_construida if area_construida > 0 else float("nan")
    pares = prog["adjacencias"]
    if pares:
        lim = prog["adjacencia"]
        soma_w = soma_adj = soma_wd = soma_wd_peso = 0.0
        for u, w_, peso in pares:
            au = [a for a in ambs if a["uso"] == u]
            aw = [a for a in ambs if a["uso"] == w_]
            soma_w += peso
            if not au or not aw:
                continue                       # uso que falta: par não atendido, sem distância
            if any(parede_comum(x["bordas"], y["bordas"], lim["parede_max"]) >= lim["comprimento_min"] - 1e-9
                   for x in au for y in aw):
                soma_adj += peso
            d = min(float((x["centro"] - y["centro"]).norm()) for x in au for y in aw)
            soma_wd += peso * d
            soma_wd_peso += peso
        r["adjacencia"] = 100 * soma_adj / soma_w
        r["distancia_ponderada"] = soma_wd / soma_wd_peso if soma_wd_peso else float("nan")
    return r


# ---------------------------------------------------------------- módulo
class Programa(Modulo):
    nome = "programa"
    depende_de = ()
    metricas = {
        "area_construida": Metrica("m²", "max", "Σ área dos pisos"),
        "ca": Metrica("-", "max", "coeficiente de aproveitamento: área construída ÷ área do lote"),
        "taxa_ocupacao": Metrica("%", "min", "projeção horizontal do projeto ÷ área do lote"),
        "altura": Metrica("m", "min", "ponto mais alto do projeto − cota do lote"),
        "recuo_min": Metrica("m", "max", "menor distância do projeto à divisa (negativa = fora do lote)"),
        "atendimento_programa": Metrica("%", "max", "Σ mín(área, meta) ÷ Σ metas, por uso"),
        "desvio_programa": Metrica("%", "min", "Σ |área − meta| ÷ Σ metas, por uso"),
        "eficiencia_planta": Metrica("%", "max", "área dos ambientes do programa ÷ área construída"),
        "adjacencia": Metrica("%", "max", "peso das adjacências desejadas atendidas (parede comum)"),
        "distancia_ponderada": Metrica("m", "min", "média ponderada da distância entre usos dos pares"),
    }

    def precisa(self, metricas):
        return {}

    def avaliar(self, cena, cache, metricas: set[str], nivel: str = "padrao"):
        prog = programa_de(getattr(cena, "opcoes", {}).get("programa"))
        if metricas & COM_LOTE and prog["lote"] is None:
            raise ValueError("programa: " + ", ".join(sorted(metricas & COM_LOTE)) + " precisa(m) do lote "
                             "(componente Lote ou 'lote' no programa.json)")
        if metricas & COM_AREAS and not prog["areas"]:
            raise ValueError("programa: atendimento/desvio precisam das áreas-meta ('areas')")
        if metricas & COM_ADJ and not prog["adjacencias"]:
            raise ValueError("programa: adjacencia/distancia_ponderada precisam de 'adjacencias'")
        dev = cena.device
        lote = (torch.tensor(prog["lote"], dtype=torch.float64, device=dev) if prog["lote"] else None)
        passo = PASSO.get(nivel, PASSO["padrao"])
        valores = defaultdict(list)
        for v in cena.variantes:
            vert = v.vertices.double().to(dev)
            proj = torch.zeros(v.faces.shape[0], dtype=torch.bool)
            for r in PROJETO:
                proj |= v.rotulos.cpu() == CODIGO[r]
            tri = v.faces.to(dev)[proj.to(dev)]
            fp = v.faces[v.rotulos.cpu() == CODIGO["piso"]]
            ac = float(normais_e_areas(v.vertices.double().cpu(), fp)[1].sum()) if fp.numel() else 0.0
            valores["area_construida"].append(ac)
            if "altura" in metricas:
                if tri.numel():
                    zs = vert[tri.flatten(), 2]
                    base = prog["z_lote"] if prog["z_lote"] is not None else float(zs.min())
                    valores["altura"].append(float(zs.max()) - base)
                else:
                    valores["altura"].append(float("nan"))
            if lote is not None:
                valores["ca"].append(ac / prog["area_lote"])
                if "taxa_ocupacao" in metricas:
                    valores["taxa_ocupacao"].append(
                        100 * area_projecao(vert[:, :2], tri, passo) / prog["area_lote"])
                if "recuo_min" in metricas:
                    valores["recuo_min"].append(recuo_minimo(vert[:, :2], tri, lote) if tri.numel()
                                                else float("nan"))
            if metricas - {"area_construida", "altura"} - COM_LOTE:
                for k, x in metricas_programa(v, prog, ac).items():
                    valores[k].append(x)
        return {m: torch.tensor(valores[m], dtype=torch.float64, device=dev) for m in metricas}


PROGRAMA = registrar(Programa())

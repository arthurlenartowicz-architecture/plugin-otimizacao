"""Protocolo JSON entre o motor (Python) e quem gera a geometria (Grasshopper, C#).

Descrição completa em rhino/grasshopper/LEIAME.md (seção "Protocolo"). Aqui ficam
só as conversões JSON ↔ objetos do motor:

  parametros  [{"nome", "tipo": "continuo"|"inteiro"|"categorico", "minimo", "maximo",
               "casas"?, "opcoes"?}]                                  → Espaco
  problema    ["max sol_luz.sda", "sol_luz.ase", "sol_luz.udi >= 50"]  → Problema
  geometria   {"malhas": {rótulo: malha | [malhas]}}                   → Variante
              {"estrutura": {...}}                                     → Estrutura
  malha       {"vertices": V, "faces": F}; V = x y z seguidos, F = i j k seguidos
              (triângulos). Cada um em lista JSON ou em base64 de float32 / int32
              little-endian (o mais rápido: sai direto de Mesh.Vertices.ToFloatArray()
              e Mesh.Faces.ToIntArray(True) do RhinoCommon).
"""
from __future__ import annotations

import base64
import re

import numpy as np
import torch

from otimiza.nucleo.cena import CODIGO, ROTULOS, Variante
from otimiza.nucleo.motor import Problema
from otimiza.nucleo.parametros import Categorico, Continuo, Espaco, Inteiro

VERSAO = 1


class ErroProtocolo(ValueError):
    """Pedido mal formado (vira HTTP 400 com a mensagem)."""


# ---------------------------------------------------------------- arrays
def array(valor, tipo: str, colunas: int) -> np.ndarray:
    """Lista JSON (plana ou aninhada) ou texto base64 → array (n, colunas)."""
    if isinstance(valor, str):
        dt = {"f4": "<f4", "f8": "<f8", "i4": "<i4"}[tipo]
        a = np.frombuffer(base64.b64decode(valor), dtype=dt)
    else:
        a = np.asarray(valor, dtype=np.float64 if tipo.startswith("f") else np.int64)
    if a.size % colunas:
        raise ErroProtocolo(f"array com {a.size} números não divide em grupos de {colunas}")
    return a.reshape(-1, colunas)


def para_b64(a: np.ndarray, tipo: str = "f4") -> str:
    return base64.b64encode(np.ascontiguousarray(a, dtype={"f4": "<f4", "f8": "<f8",
                                                         "i4": "<i4"}[tipo]).tobytes()).decode()


# ---------------------------------------------------------------- parâmetros e problema
def espaco_de(lista: list[dict]) -> Espaco:
    ps = []
    for d in lista:
        tipo, nome = d.get("tipo"), d.get("nome")
        if not nome:
            raise ErroProtocolo("parâmetro sem nome")
        if tipo == "continuo":
            casas = d.get("casas")
            ps.append(Continuo(nome, float(d["minimo"]), float(d["maximo"]),
                               None if casas is None else int(casas)))
        elif tipo == "inteiro":
            ps.append(Inteiro(nome, int(d["minimo"]), int(d["maximo"])))
        elif tipo == "categorico":
            ps.append(Categorico(nome, tuple(d["opcoes"])))
        else:
            raise ErroProtocolo(f"{nome}: tipo deve ser continuo, inteiro ou categorico, não {tipo!r}")
    return Espaco(ps)


_LINHA = re.compile(r"^\s*(?:(min|max)\s+)?([\w]+\.[\w]+)\s*(?:(<=|>=)\s*([-+0-9.eE]+))?\s*$")


def problema_de(linhas) -> Problema:
    """Uma linha por objetivo ou restrição (texto de um Panel do GH):
         sol_luz.sda           objetivo no sentido padrão da métrica
         max sol_luz.udi       objetivo com sentido escolhido
         sol_luz.ase <= 10     restrição (não é objetivo)
    Linhas vazias e começando com # são ignoradas. Aceita também um texto só."""
    if isinstance(linhas, str):
        linhas = [linhas]
    # cada item pode ter várias linhas (Panel multilinha do GH manda um texto só)
    linhas = [sub for item in linhas for sub in str(item).splitlines()]
    objetivos, sentidos, restricoes = [], {}, []
    for bruta in linhas:
        linha = str(bruta).split("#")[0].strip()
        if not linha:
            continue
        m = _LINHA.match(linha)
        if not m:
            raise ErroProtocolo(f"linha do problema não entendida: {bruta!r} "
                                "(use 'max modulo.metrica' ou 'modulo.metrica <= valor')")
        sentido, chave, op, lim = m.groups()
        if op:
            restricoes.append((chave, op, float(lim)))
        else:
            objetivos.append(chave)
            if sentido:
                sentidos[chave] = sentido
    if not objetivos:
        raise ErroProtocolo("o problema precisa de pelo menos um objetivo")
    return Problema(objetivos, sentidos, restricoes)


# ---------------------------------------------------------------- geometria (sol_luz)
def materiais_de(d: dict | None) -> tuple[dict, float]:
    """{"fachada": {"rho": 0.5}, "vidro": {"tau": 0.64}, ...} → (rho por rótulo, τ do vidro)."""
    from otimiza.nucleo.prototipos import RHO, TAU_VIDRO
    rho, tau = dict(RHO), TAU_VIDRO
    for rot, m in (d or {}).items():
        if rot not in CODIGO:
            raise ErroProtocolo(f"material de rótulo desconhecido {rot!r} (use {ROTULOS})")
        if "rho" in m:
            rho[rot] = float(m["rho"])
        if rot == "vidro" and "tau" in m:
            tau = float(m["tau"])
    return rho, tau


def _malhas(d: dict) -> list[tuple[np.ndarray, np.ndarray]]:
    itens = d if isinstance(d, list) else [d]
    return [(array(m["vertices"], m.get("tipo_vertices", "f4"), 3),
             array(m["faces"], "i4", 3)) for m in itens if m]


def variante_de(malhas: dict, escala: float = 1.0, materiais: dict | None = None,
                fixas: dict | None = None) -> Variante:
    """Junta as malhas por rótulo (e as `fixas`, mesmo formato, mandadas uma vez só)
    numa Variante em metros. `escala` = unidade do modelo → metro."""
    rho, tau = materiais_de(materiais)
    vs, fs, rot = [], [], []
    base = 0
    for grupo in (malhas or {}, fixas or {}):
        for r, d in grupo.items():
            if r not in CODIGO:
                raise ErroProtocolo(f"rótulo desconhecido {r!r} (use {ROTULOS})")
            for v, f in _malhas(d):
                if f.size and (f.min() < 0 or f.max() >= len(v)):
                    raise ErroProtocolo(f"{r}: face aponta para vértice inexistente")
                vs.append(v.astype(np.float64) * escala)
                fs.append(f.astype(np.int64) + base)
                rot += [r] * len(f)
                base += len(v)
    if not fs or not sum(len(f) for f in fs):
        raise ErroProtocolo("variante sem faces")
    rotulos = torch.tensor([CODIGO[r] for r in rot])
    refl = torch.tensor([rho[r] for r in rot], dtype=torch.float32)
    trans = torch.tensor([tau if r == "vidro" else 0.0 for r in rot], dtype=torch.float32)
    return Variante(torch.from_numpy(np.concatenate(vs)), torch.from_numpy(np.concatenate(fs)),
                    rotulos, refl, trans)


def malhas_de_variante(v: Variante, b64: bool = True) -> dict:
    """Inverso de `variante_de` (o que um cliente mandaria); usado nos testes e exemplos."""
    saida = {}
    vert = v.vertices.double().cpu().numpy()
    for r in ROTULOS:
        m = (v.rotulos == CODIGO[r]).cpu().numpy()
        if not m.any():
            continue
        f = v.faces.cpu().numpy()[m]
        usados, novo = np.unique(f, return_inverse=True)
        vv, ff = vert[usados], novo.reshape(-1, 3)
        saida[r] = ({"vertices": para_b64(vv, "f8"), "tipo_vertices": "f8", "faces": para_b64(ff, "i4")}
                    if b64 else {"vertices": vv.tolist(), "faces": ff.tolist()})
    return saida


# ---------------------------------------------------------------- estrutura
_APOIOS = {"engaste": (True,) * 6, "articulado": (True, True, True, False, False, False),
           "rolete_z": (False, False, True, False, False, False)}


def _secao(s):
    from otimiza.modulos.estrutura.fem import Secao, retangulo, tubo_circular
    from otimiza.modulos.estrutura.prototipos import TUBOS
    if isinstance(s, str):
        if s not in TUBOS:
            raise ErroProtocolo(f"seção {s!r} fora do catálogo ({sorted(TUBOS)})")
        return TUBOS[s]
    if "D" in s:
        return tubo_circular(float(s["D"]), float(s["t"]))
    if "b" in s:
        return retangulo(float(s["b"]), float(s["h"]))
    return Secao(**{k: (bool(v) if k == "circular" else float(v)) for k, v in s.items()})


def estrutura_de(d: dict, escala: float = 1.0, tolerancia: float = 1e-4):
    """Barras como linhas (x1 y1 z1 x2 y2 z2 seguidos); nós = pontas unidas por proximidade.

    {"barras": [...], "secao": "RO114.3/4" | {"D","t"} | {"b","h"} | [uma por barra],
     "trelica": bool, "material": {"E","G","rho","fy"}?, "gravidade": 9.81,
     "apoios": [{"pontos": [...], "condicoes": [6 bools] | "engaste" | "articulado"}],
     "cargas": [{"pontos": [...], "forca": [Fx,Fy,Fz], "momento": [Mx,My,Mz]?}]}
    Unidades: comprimento do modelo × escala = m; kN, kN·m; seção em m."""
    from otimiza.modulos.estrutura.fem import ACO_S235, Estrutura, Material
    lin = array(d["barras"], d.get("tipo_barras", "f8"), 6).astype(np.float64) * escala
    pontas = lin.reshape(-1, 3)
    chave = np.round(pontas / tolerancia).astype(np.int64)
    _, primeiro, inv = np.unique(chave, axis=0, return_index=True, return_inverse=True)
    inv = inv.reshape(-1)
    # nós na ordem de 1ª aparição (como o Karamba numera)
    ordem = np.argsort(primeiro, kind="stable")
    novo = np.empty_like(ordem)
    novo[ordem] = np.arange(len(ordem))
    nos = pontas[primeiro[ordem]]
    barras = novo[inv].reshape(-1, 2)
    if (barras[:, 0] == barras[:, 1]).any():
        raise ErroProtocolo("barra de comprimento zero")

    def no_de(pontos) -> list[int]:
        p = array(pontos, "f8", 3) * escala
        k = np.round(p / tolerancia).astype(np.int64)
        idx = {tuple(c): i for i, c in enumerate(np.round(nos / tolerancia).astype(np.int64))}
        saida = []
        for c, q in zip(k, p):
            if tuple(c) in idx:
                saida.append(idx[tuple(c)])
            else:   # tolerância de arredondamento: o nó mais perto, se bem perto
                dist = np.linalg.norm(nos - q, axis=1)
                if dist.min() > 10 * tolerancia:
                    raise ErroProtocolo(f"ponto {q.tolist()} não coincide com nenhum nó")
                saida.append(int(dist.argmin()))
        return saida

    apoios = {}
    for g in d.get("apoios", []):
        c = g.get("condicoes", "articulado")
        c = _APOIOS[c] if isinstance(c, str) else tuple(bool(x) for x in c)
        for n in no_de(g["pontos"]):
            apoios[n] = tuple(a or b for a, b in zip(apoios.get(n, (False,) * 6), c))
    cargas = {}
    for g in d.get("cargas", []):
        c = np.array(list(g.get("forca", [0, 0, 0])) + list(g.get("momento", [0, 0, 0])), float)
        for n in no_de(g["pontos"]):
            cargas[n] = cargas.get(n, np.zeros(6)) + c
    s = d.get("secao", "RO114.3/4")
    secao = [_secao(x) for x in s] if isinstance(s, list) else _secao(s)
    if isinstance(secao, list) and len(secao) != len(barras):
        raise ErroProtocolo(f"{len(secao)} seções para {len(barras)} barras")
    mat = Material(**{k: float(v) for k, v in d["material"].items()}) if d.get("material") else ACO_S235
    return Estrutura.criar(nos, barras, secao, mat, trelica=bool(d.get("trelica", False)),
                           apoios=apoios, cargas={n: tuple(c) for n, c in cargas.items()},
                           gravidade=float(d.get("gravidade", 9.81)))


def estrutura_para_dict(s) -> dict:
    """Estrutura → formato do protocolo (linhas, apoios e cargas por ponto); para testes."""
    nos = s.nos.double().cpu().numpy()
    lin = np.concatenate([nos[s.barras[:, 0].cpu().numpy()], nos[s.barras[:, 1].cpu().numpy()]], 1)
    grupos: dict[tuple, list] = {}
    for n, c in enumerate(s.apoios.cpu().numpy()):
        if c.any():
            grupos.setdefault(tuple(bool(x) for x in c), []).append(nos[n].tolist())
    cargas = [{"pontos": [nos[n].tolist()], "forca": c[:3].tolist(), "momento": c[3:].tolist()}
              for n, c in enumerate(s.cargas.double().cpu().numpy()) if np.any(c)]
    return {"barras": lin.tolist(), "trelica": bool(s.trelica.all()), "gravidade": s.gravidade,
            "apoios": [{"pontos": p, "condicoes": list(c)} for c, p in grupos.items()],
            "cargas": cargas}


def geometria_de(g: dict, escala: float = 1.0, materiais: dict | None = None,
                 fixas: dict | None = None):
    """Uma geometria do protocolo → (objeto para avaliar, objeto para exportar).
    Para malhas, o de exportar não leva as `fixas` (contexto não vai 200 vezes ao json)."""
    if "estrutura" in g:
        e = estrutura_de(g["estrutura"], escala)
        return e, e
    if "malhas" in g:
        v = variante_de(g["malhas"], escala, materiais, fixas)
        return v, (variante_de(g["malhas"], escala, materiais) if fixas else v)
    raise ErroProtocolo("geometria precisa de 'malhas' ou 'estrutura'")

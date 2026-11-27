"""Geometrias paramétricas simples para testes, benchmark e protótipos de problema."""
from __future__ import annotations

import math

import torch

from otimiza.nucleo.cena import CODIGO, Variante

# materiais padrão do Honeybee (validados na 1c)
RHO = {"fachada": 0.5, "interno": 0.5, "piso": 0.2, "cobertura": 0.8, "contexto": 0.35,
       "entorno": 0.2, "vidro": 0.0}
TAU_VIDRO = 0.64
FS_VIDRO = 0.42  # fator solar do Generic Double Pane do Honeybee (τ 0,64)


def _retangulo(p0, u, v):
    """Retângulo p0, p0+u, p0+u+v, p0+v; normal = u × v."""
    p0, u, v = (torch.as_tensor(x, dtype=torch.float64) for x in (p0, u, v))
    vs = torch.stack([p0, p0 + u, p0 + u + v, p0 + v])
    return vs, torch.tensor([[0, 1, 2], [0, 2, 3]])


class _Montador:
    def __init__(self):
        self.v, self.f, self.r = [], [], []
        self.base = 0

    def add(self, malha, rotulo):
        vs, fs = malha
        self.v.append(vs)
        self.f.append(fs + self.base)
        self.r += [rotulo] * fs.shape[0]
        self.base += vs.shape[0]

    def variante(self, tau_vidro=TAU_VIDRO, rho=None) -> Variante:
        rho = {**RHO, **(rho or {})}
        rot = torch.tensor([CODIGO[r] for r in self.r])
        refl = torch.tensor([rho[r] for r in self.r], dtype=torch.float32)
        tau = torch.tensor([tau_vidro if r == "vidro" else 0.0 for r in self.r], dtype=torch.float32)
        return Variante(torch.cat(self.v), torch.cat(self.f), rot, refl, tau)


def _parede(m: _Montador, p0, u, altura, razao, peitoril=0.8, margem=0.2):
    """Parede (u horizontal, normal = u × z para fora) com uma janela centrada."""
    p0 = torch.as_tensor(p0, dtype=torch.float64)
    u = torch.as_tensor(u, dtype=torch.float64)
    largura = u.norm().item()
    e = u / largura
    z = torch.tensor([0, 0, 1.0], dtype=torch.float64)
    if razao <= 0:
        m.add(_retangulo(p0, u, z * altura), "fachada")
        return
    lj = max(largura - 2 * margem, 0.1)
    hj = min(razao * largura * altura / lj, altura - 0.1)
    pz = min(peitoril, altura - hj - 0.05)
    pz = max(pz, 0.05)
    x0 = (largura - lj) / 2
    # janela e as quatro faixas de parede em volta
    m.add(_retangulo(p0 + e * x0 + z * pz, e * lj, z * hj), "vidro")
    m.add(_retangulo(p0, e * largura, z * pz), "fachada")                                 # abaixo
    m.add(_retangulo(p0 + z * (pz + hj), e * largura, z * (altura - pz - hj)), "fachada")  # acima
    m.add(_retangulo(p0 + z * pz, e * x0, z * hj), "fachada")                             # esquerda
    m.add(_retangulo(p0 + e * (x0 + lj) + z * pz, e * x0, z * hj), "fachada")             # direita


def predio(largura=6.0, profundidade=8.0, altura=3.0, janelas=None, origem=(0.0, 0.0),
           rotacao_graus=0.0, contexto=(), entorno=(), tau_vidro=TAU_VIDRO, rho=None) -> Variante:
    """Uma sala/pavimento em caixa com janelas por fachada.

    janelas: {"N": razão, "L": ..., "S": ..., "O": ...} (padrão: 40% ao norte).
    contexto / entorno: listas de (vertices, faces) já em metros.
    rotacao_graus: gira o prédio em torno da origem (sentido anti-horário visto de cima).
    """
    janelas = {"N": 0.4} if janelas is None else janelas
    W, D, H = largura, profundidade, altura
    m = _Montador()
    _parede(m, (0, 0, 0), (W, 0, 0), H, janelas.get("S", 0.0))        # sul, normal −y
    _parede(m, (W, 0, 0), (0, D, 0), H, janelas.get("L", 0.0))        # leste, normal +x
    _parede(m, (W, D, 0), (-W, 0, 0), H, janelas.get("N", 0.0))       # norte, normal +y
    _parede(m, (0, D, 0), (0, -D, 0), H, janelas.get("O", 0.0))       # oeste, normal −x
    m.add(_retangulo((0, 0, 0), (0, D, 0), (W, 0, 0)), "piso")          # normal −z (fora)
    m.add(_retangulo((0, 0, H), (W, 0, 0), (0, D, 0)), "cobertura")     # normal +z
    n_proj = m.base
    for malha in contexto:
        m.add(malha, "contexto")
    for malha in entorno:
        m.add(malha, "entorno")
    var = m.variante(tau_vidro, rho)
    # gira e desloca só o prédio
    a = math.radians(rotacao_graus)
    rot = torch.tensor([[math.cos(a), -math.sin(a), 0], [math.sin(a), math.cos(a), 0], [0, 0, 1.0]],
                       dtype=torch.float64)
    v = var.vertices.clone()
    v[:n_proj] = v[:n_proj] @ rot.T + torch.tensor([origem[0], origem[1], 0.0], dtype=torch.float64)
    var.vertices = v
    return var


def predio_de(p: dict, **fixos) -> Variante:
    """Gerador para o otimizador: chaves de `predio` ('largura', 'profundidade', 'altura',
    'rotacao_graus', ...) mais 'janela_N', 'janela_L', 'janela_S', 'janela_O' (razão
    janela/parede de cada fachada). `fixos` completa o que não for parâmetro (ex.: contexto)."""
    q = {**fixos, **p}
    janelas = dict(q.pop("janelas", None) or {})
    for f in "NLSO":
        if f"janela_{f}" in q:
            janelas[f] = q.pop(f"janela_{f}")
    return predio(janelas=janelas, **q)

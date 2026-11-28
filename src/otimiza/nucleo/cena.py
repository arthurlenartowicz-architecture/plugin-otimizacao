"""CenaLote: N variantes de projeto avaliadas juntas na GPU.

Cada variante é uma malha triangulada; cada face tem um rótulo (papel) e um
material. Rótulos usados pelos módulos:
  fachada   paredes externas opacas (radiação, sombra)
  vidro     aberturas (transmitem luz; contam como fachada na radiação)
  cobertura telhados (radiação, FV)
  piso      pisos internos (sensores de luz natural a 0,8 m)
  interno   outras superfícies internas (só refletem)
  contexto  entorno existente (faz sombra, não é avaliado)
  entorno   entorno sensível (sensores de perda de sol: vizinhos, praças)
Pisos podem ter um ambiente (uso: sala, quarto…) para o módulo programa
(`Variante.ambientes` / `usos`).

O módulo estrutura usa `estruturas` (modelo de barras de cada variante), à
parte da malha; uma cena só de estrutura pode ter `variantes=[]` e clima None.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import torch

from otimiza.nucleo.clima import Clima
from otimiza.nucleo.raios import Cena, dispositivo_padrao

ROTULOS = ("fachada", "vidro", "cobertura", "piso", "interno", "contexto", "entorno")
CODIGO = {r: i for i, r in enumerate(ROTULOS)}


@dataclass
class Variante:
    vertices: torch.Tensor       # (Vt, 3) metros
    faces: torch.Tensor          # (F, 3)
    rotulos: torch.Tensor        # (F,) códigos de ROTULOS
    refletancia: torch.Tensor    # (F,) visível
    transmitancia: torch.Tensor  # (F,) τ normal (> 0 = vidro)
    fator_solar: float = 0.42    # FS (SHGC) dos vidros; padrão = Generic Double Pane do Honeybee
    # ambientes (módulo programa): índice do ambiente de cada face (−1 = nenhum) e o uso de cada
    # ambiente (ex.: ("sala", "quarto", "quarto")); só os pisos costumam ter ambiente
    ambientes: torch.Tensor | None = None
    usos: tuple[str, ...] = ()

    def __post_init__(self):
        f = self.faces.shape[0]
        for nome in ("rotulos", "refletancia", "transmitancia"):
            if getattr(self, nome).shape[0] != f:
                raise ValueError(f"{nome} precisa de um valor por face ({f})")
        if self.ambientes is not None:
            if self.ambientes.shape[0] != f:
                raise ValueError(f"ambientes precisa de um valor por face ({f})")
            if self.ambientes.numel() and int(self.ambientes.max()) >= len(self.usos):
                raise ValueError("ambiente sem uso: usos precisa de um nome por ambiente")


@dataclass
class CenaLote:
    variantes: list[Variante]
    clima: Clima
    device: str = field(default_factory=dispositivo_padrao)
    estruturas: list | None = None   # uma Estrutura (modulos.estrutura.fem) por variante
    opcoes: dict = field(default_factory=dict)  # ajustes por módulo, ex.: {"carbono_custo": {...}}
    _cena: Cena | None = None
    _sem_projeto: Cena | None = None

    @property
    def n(self) -> int:
        return len(self.variantes) if self.variantes else len(self.estruturas or [])

    def raios(self) -> Cena:
        """Cena de raios com todas as variantes (construída uma vez)."""
        if self._cena is None:
            self._cena = Cena([(v.vertices, v.faces) for v in self.variantes], self.device)
        return self._cena

    def raios_sem_projeto(self) -> Cena:
        """Só contexto + entorno de cada variante (para a perda de sol no entorno)."""
        if self._sem_projeto is None:
            malhas = []
            for v in self.variantes:
                m = (v.rotulos == CODIGO["contexto"]) | (v.rotulos == CODIGO["entorno"])
                f = v.faces[m] if m.any() else v.faces[:0]
                if f.shape[0] == 0:  # cena vazia: um triângulo bem longe
                    malhas.append((torch.tensor([[1e6, 1e6, 0], [1e6 + 1, 1e6, 0], [1e6, 1e6 + 1, 0.0]]),
                                   torch.tensor([[0, 1, 2]])))
                else:
                    malhas.append((v.vertices, f))
            self._sem_projeto = Cena(malhas, self.device)
        return self._sem_projeto

    def materiais(self):
        from otimiza.modulos.sol_luz.motor_b import Materiais  # aqui para evitar import circular

        return Materiais.de_listas([(v.refletancia, v.transmitancia) for v in self.variantes])

    def faces_com(self, i: int, *rotulos: str) -> torch.Tensor:
        """Índices das faces da variante i com algum dos rótulos."""
        v = self.variantes[i]
        m = torch.zeros(v.faces.shape[0], dtype=torch.bool)
        for r in rotulos:
            m |= v.rotulos == CODIGO[r]
        return m.nonzero().flatten()


def media_por_variante(valores: torch.Tensor, variante: torch.Tensor, n: int,
                       pesos: torch.Tensor | None = None) -> torch.Tensor:
    """(n,) média (ponderada) dos valores de cada variante; NaN se não houver sensores."""
    dev = valores.device
    variante = variante.to(dev).long()
    w = torch.ones_like(valores) if pesos is None else pesos.to(valores)
    soma = torch.zeros(n, dtype=valores.dtype, device=dev).index_add_(0, variante, valores * w)
    peso = torch.zeros(n, dtype=valores.dtype, device=dev).index_add_(0, variante, w)
    return torch.where(peso > 0, soma / peso.clamp(min=1e-30), torch.full_like(soma, float("nan")))


def soma_por_variante(valores: torch.Tensor, variante: torch.Tensor, n: int) -> torch.Tensor:
    dev = valores.device
    return torch.zeros(n, dtype=valores.dtype, device=dev).index_add_(0, variante.to(dev).long(), valores)

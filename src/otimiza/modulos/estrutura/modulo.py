"""Módulo estrutura: FEM de barras (treliças e pórticos 3D) em lote no contrato comum.

Lê `cena.estruturas` (uma `Estrutura` por variante). Análise linear elástica,
um caso de carga (cargas nodais + distribuídas + peso próprio). Variante
instável (mecanismo) → NaN em todas as métricas.
"""
from __future__ import annotations

import torch

from otimiza.modulos.estrutura.fem import resolver
from otimiza.nucleo.contrato import Metrica, Modulo, registrar

# elementos por barra de pórtico (afeta flambagem e flecha no meio das barras)
NIVEIS = {"rapido": dict(n_sub=1), "padrao": dict(n_sub=2), "validacao": dict(n_sub=4)}


class Estrutura(Modulo):
    nome = "estrutura"
    depende_de = ()
    metricas = {
        "deslocamento_max": Metrica("cm", "min", "maior deslocamento nodal (translação)"),
        "energia": Metrica("kN·m", "min", "energia de deformação ½uᵀF (flexibilidade)"),
        "massa": Metrica("kg", "min", "massa total das barras"),
        "utilizacao": Metrica("%", "min", "máx. tensão elástica (|N|/A + flexão) ÷ fy"),
        "flambagem": Metrica("-", "max", "1º fator de carga de flambagem linear"),
    }

    def avaliar(self, cena, cache, metricas: set[str], nivel: str = "padrao"):
        if not cena.estruturas or len(cena.estruturas) != cena.n:
            raise ValueError("o módulo estrutura precisa de cena.estruturas (uma por variante)")
        r = resolver(cena.estruturas, n_sub=NIVEIS[nivel]["n_sub"],
                     flambagem="flambagem" in metricas, device=cena.device)
        saida = {
            "deslocamento_max": r.desloc_max * 100,
            "energia": r.energia,
            "massa": r.massa,
            "utilizacao": r.utilizacao * 100,
            "flambagem": r.flambagem,
        }
        return {m: saida[m].float() for m in metricas}


ESTRUTURA = registrar(Estrutura())

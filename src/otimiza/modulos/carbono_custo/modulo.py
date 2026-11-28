"""Módulo carbono_custo (Etapa 4d): carbono incorporado e operacional, custo de construção,
de operação e do ciclo de vida.

- Incorporado (A1–A3): Σ área de cada elemento × kgCO2e/m² da construção (camadas × densidade
  × fator do material, fatores.json) + barras da `cena.estruturas`: massa × fator do aço.
  Elementos pelas mesmas áreas do modelo RC (`termico.zona.geometria_rc`): parede = fachada,
  vidro, cobertura, piso na base = piso no solo, pisos acima = lajes (área contada 1×).
  Faces "interno", "contexto" e "entorno" não entram; esquadrias também não.
- Operacional: `termico.consumo` (kWh/ano) × fator de emissão da rede; custo = × tarifa.
- Total em N anos: incorporado + N × operacional (sem desconto, como no carbono do ciclo de vida);
  custo do ciclo de vida = construção + operação trazida a valor presente (taxa real i).
Fatores trocados pelo usuário chegam em `cena.opcoes["carbono_custo"]` (ver fatores.py).
Não usa a GPU nem raios: os três níveis dão o mesmo valor.
"""
from __future__ import annotations

import torch

from otimiza.modulos.carbono_custo.fatores import CONSTRUCOES, fator_anuidade, fatores_de, por_m2
from otimiza.nucleo.contrato import Metrica, Modulo, registrar

_OPERACIONAL = {"carbono_operacional", "carbono_total", "custo_operacional", "custo_ciclo_vida"}
_POR_M2 = {"carbono_incorporado_m2", "custo_construcao_m2"}
# métricas que não precisam do clima (só geometria e fatores)
SEM_CLIMA = {"carbono_incorporado", "carbono_incorporado_m2", "custo_construcao", "custo_construcao_m2"}


def areas(cena) -> dict[str, torch.Tensor]:
    """(V,) m² de cada construção + área de piso; zeros para variantes só de estrutura."""
    n = cena.n
    if not cena.variantes:
        z = torch.zeros(n, dtype=torch.float64)
        return {**{c: z.clone() for c in CONSTRUCOES}, "area_piso": z.clone()}
    from otimiza.modulos.termico.zona import geometria_rc
    g = geometria_rc(cena, piso_base="solo")
    return {"parede": g["parede"], "vidro": g["vidro"], "cobertura": g["cobertura"],
            "piso_solo": g["solo"], "piso_interno": g["piso_interno"] / 2,
            "area_piso": g["area_piso"]}


def massa_estrutura(cena) -> torch.Tensor:
    n = cena.n
    if not cena.estruturas:
        return torch.zeros(n, dtype=torch.float64)
    return torch.tensor([float(s.massa()) for s in cena.estruturas], dtype=torch.float64)


class CarbonoCusto(Modulo):
    nome = "carbono_custo"
    depende_de = ("termico",)
    metricas = {
        "carbono_incorporado": Metrica("kgCO2e", "min", "A1–A3 da envoltória, lajes e estrutura de aço"),
        "carbono_incorporado_m2": Metrica("kgCO2e/m²", "min", "carbono incorporado ÷ área de piso"),
        "carbono_operacional": Metrica("kgCO2e/ano", "min", "termico.consumo × fator da rede (SIN)"),
        "carbono_total": Metrica("kgCO2e", "min", "incorporado + N anos × operacional"),
        "custo_construcao": Metrica("R$", "min", "Σ área × R$/m² por elemento + aço × R$/kg"),
        "custo_construcao_m2": Metrica("R$/m²", "min", "custo de construção ÷ área de piso"),
        "custo_operacional": Metrica("R$/ano", "min", "termico.consumo × tarifa"),
        "custo_ciclo_vida": Metrica("R$", "min", "construção + operação em N anos a valor presente"),
    }

    def precisa(self, metricas):
        return {"termico": {"consumo"}} if metricas & _OPERACIONAL else {}

    def avaliar(self, cena, cache, metricas: set[str], nivel: str = "padrao"):
        f = fatores_de(getattr(cena, "opcoes", {}).get("carbono_custo"))
        a = areas(cena)
        m_aco = massa_estrutura(cena)
        fator_aco = float(f["materiais"][f["estrutura"]["material"]]["kgco2e_kg"])
        incorporado = m_aco * fator_aco
        construcao = m_aco * float(f["custo"]["aco_kg"])
        for c in CONSTRUCOES:
            incorporado = incorporado + a[c] * por_m2(f, c)[1]
            construcao = construcao + a[c] * float(f["custo"][c])
        piso = a["area_piso"]

        def por_piso(x):
            return torch.where(piso > 0, x / piso.clamp(min=1e-30), torch.full_like(x, float("nan")))

        r = {"carbono_incorporado": incorporado, "custo_construcao": construcao,
             "carbono_incorporado_m2": por_piso(incorporado), "custo_construcao_m2": por_piso(construcao)}
        if metricas & _OPERACIONAL:
            consumo = cache.resultados["termico.consumo"].double().cpu()
            anos = int(round(float(f["anos"])))
            op_c = consumo * float(f["energia"]["kgco2e_kwh"])
            op_r = consumo * float(f["energia"]["tarifa"])
            r.update(carbono_operacional=op_c, carbono_total=incorporado + anos * op_c,
                     custo_operacional=op_r,
                     custo_ciclo_vida=construcao + fator_anuidade(anos, float(f["taxa_desconto"])) * op_r)
        return {m: r[m].to(cena.device) for m in metricas}


CARBONO_CUSTO = registrar(CarbonoCusto())

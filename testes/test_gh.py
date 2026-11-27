"""Validação GH × protótipo: a sala de exemplo montada no Grasshopper (Breps de
sala_exemplo.py, malhados pelo componente Otimizar) contra `prototipos.predio_de` com os
mesmos parâmetros.

O arquivo vem do botão `avaliar` do otimizar.gh (caso = sala_gh):
testes/referencias/gh/variante_sala_gh.json — pedido do /avaliar (geometria em base64,
escala, contexto) + genoma (valores dos sliders) + tempo do lado do GH.
A triangulação do GH é outra (parede com furo × faixas), então os sensores mudam de
lugar: áreas iguais, métricas com tolerância.
"""
import json
from pathlib import Path

import numpy as np
import pytest

from otimiza.nucleo.cena import CODIGO, ROTULOS
from otimiza.nucleo.geometria import caixa
from otimiza.servidor import protocolo as pr
from testes.conftest import EPWS_REAIS

ARQ = Path(__file__).parent / "referencias" / "gh" / "variante_sala_gh.json"
CHAVES = ["sol_luz.radiacao_fachada", "sol_luz.radiacao_cobertura", "sol_luz.ase",
          "sol_luz.da", "sol_luz.sda", "sol_luz.udi"]
# tolerâncias: radiação relativa; as outras em pontos percentuais
TOL = {"sol_luz.radiacao_fachada": 0.03, "sol_luz.radiacao_cobertura": 0.01,
       "sol_luz.ase": 3.0, "sol_luz.da": 2.0, "sol_luz.sda": 5.0, "sol_luz.udi": 2.0}

pytestmark = pytest.mark.skipif(not ARQ.is_file() or not EPWS_REAIS,
                                reason="rode 'avaliar' no otimizar.gh (caso = sala_gh) antes")


def _prototipo(genoma):
    from otimiza.nucleo.prototipos import predio_de
    p = {k: float(genoma[k]) for k in ("janela_N", "janela_L", "janela_S", "janela_O",
                                       "rotacao_graus")}
    return predio_de(p, largura=6.0, profundidade=12.0, altura=3.0,
                     contexto=[caixa(-10, 14, 0, 16, 24, 30)])


def _area(v, r):
    import torch
    f = v.faces[v.rotulos == CODIGO[r]]
    p = v.vertices.double()
    return float(torch.linalg.cross(p[f[:, 1]] - p[f[:, 0]], p[f[:, 2]] - p[f[:, 0]]).norm(dim=1).sum() / 2)


def test_geometria_do_gh_igual_ao_prototipo():
    d = json.loads(ARQ.read_text(encoding="utf-8"))
    cfg_fixas = {r: x for r, x in d["fixas"]["malhas"].items()}
    gh = pr.variante_de(d["geometrias"][0]["malhas"], d["escala"], fixas=cfg_fixas)
    ref = _prototipo(d["genoma"])
    print("\ngenoma", d["genoma"], "| GH: %.1f ms para malhar e converter" % (1000 * d["tempo_gh_s"]))
    for r in ROTULOS:
        print(f"{r:10s} área GH {_area(gh, r):9.3f}  protótipo {_area(ref, r):9.3f}")
        assert _area(gh, r) == pytest.approx(_area(ref, r), rel=1e-4, abs=1e-6), r
    # normais para fora: soma das normais × área de cada rótulo do projeto bate
    import torch
    for r in ("fachada", "vidro", "piso", "cobertura"):
        def soma(v):
            f = v.faces[v.rotulos == CODIGO[r]]
            p = v.vertices.double()
            return torch.linalg.cross(p[f[:, 1]] - p[f[:, 0]], p[f[:, 2]] - p[f[:, 0]]).sum(0) / 2
        assert torch.allclose(soma(gh), soma(ref), atol=1e-3), f"normais de {r}"


def test_metricas_do_gh_iguais_ao_prototipo():
    import otimiza.modulos.sol_luz  # noqa: F401
    from otimiza.nucleo.cena import CenaLote
    from otimiza.nucleo.clima import ler_epw
    from otimiza.nucleo.motor import avaliar
    from otimiza.servidor.servico import Servico
    d = json.loads(ARQ.read_text(encoding="utf-8"))
    nivel = d.get("nivel", "rapido")
    sv = Servico()
    via = sv.avaliar(dict(d, metricas=CHAVES, epw=str(EPWS_REAIS[0])))
    sv.encerrar()
    ref = avaliar(CenaLote(variantes=[_prototipo(d["genoma"])], clima=ler_epw(EPWS_REAIS[0])),
                  CHAVES, nivel)
    for k in CHAVES:
        a, b = via["valores"][k][0], float(ref[k][0])
        print(f"{k:28s} GH {a:9.3f}  protótipo {b:9.3f}")
        if k.startswith("sol_luz.radiacao"):
            assert a == pytest.approx(b, rel=TOL[k]), k
        else:
            assert abs(a - b) <= TOL[k], k
    # o que o servidor deu na hora (no Rhino) = o que dá agora
    for k, v in d.get("valores_gh", {}).items():
        if k in via["valores"] and v[0] is not None:
            assert np.isclose(via["valores"][k][0], v[0], rtol=1e-3, atol=0.5), k

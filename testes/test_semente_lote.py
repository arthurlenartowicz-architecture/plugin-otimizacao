"""O resultado do path tracing (motores B e C) de uma variante não depende do lote.

A semente de cada raio vem da posição do sensor (ou do pixel da vista) dentro da sua
própria variante, não da posição no lote. Antes, o DA da mesma sala mudava em média
0,09 ponto (até ~1,6) no nível rápido só por trocar as outras salas do lote.
"""
import pytest
import torch

import otimiza.modulos.sol_luz  # noqa: F401
from otimiza.nucleo.geometria import caixa
from otimiza.nucleo.motor import avaliar
from otimiza.nucleo.parametros import cena_de
from otimiza.nucleo.prototipos import predio_de
from testes.conftest import EPWS_REAIS

pytestmark = pytest.mark.skipif(not EPWS_REAIS, reason="precisa de um EPW real em data/epw")

# motor B (da, udi, df, medi_horas) e motor C (ga)
CHAVES = ["sol_luz.da", "sol_luz.udi", "sol_luz.df", "sol_luz.medi_horas", "sol_luz.ga"]


def _sala(p):
    return predio_de(p, largura=6.0, profundidade=12.0, altura=3.0,
                     contexto=[caixa(-10, 14, 0, 16, 24, 30)])


def _p(n, l, s, o, rot):
    return dict(janela_N=n, janela_L=l, janela_S=s, janela_O=o, rotacao_graus=rot)


A = _p(0.6, 0.3, 0.7, 0.4, 15)     # janelas grandes: GA < 100 (motor C testado)
OUTRAS = [_p(0.7, 0.3, 0.6, 0.2, 10), _p(0.1, 0.8, 0.0, 0.5, -30), _p(0.6, 0.6, 0.6, 0.6, 45)]


def test_variante_igual_sozinha_e_em_lotes():
    from otimiza.nucleo.cache import Cache
    from otimiza.nucleo.clima import ler_epw
    clima, cache = ler_epw(str(EPWS_REAIS[0])), Cache()

    def da_a(params, i):
        cache.resultados.clear()
        r = avaliar(cena_de(_sala, params, clima=clima), CHAVES, "rapido", cache)
        return {k: float(v[i]) for k, v in r.items()}

    sozinha = da_a([A], 0)
    no_inicio = da_a([A] + OUTRAS, 0)
    no_fim = da_a(OUTRAS + [A], len(OUTRAS))
    no_meio = da_a(OUTRAS[:1] + [A] + OUTRAS[1:], 1)
    for k in CHAVES:
        assert k != "sol_luz.ga" or sozinha[k] < 99.0, "sala sem ofuscamento"
        print(f"{k:22s} sozinha {sozinha[k]:9.4f}  início {no_inicio[k]:9.4f}  "
              f"fim {no_fim[k]:9.4f}  meio {no_meio[k]:9.4f}")
        # a soma atômica em float32 muda de ordem entre lançamentos: só arredondamento
        for outro in (no_inicio, no_fim, no_meio):
            assert outro[k] == pytest.approx(sozinha[k], rel=1e-5, abs=1e-4), k


def test_indice_local():
    from otimiza.nucleo.raios import Cena
    cena = Cena.__new__(Cena)
    cena.device = "cpu"
    v = torch.tensor([2, 0, 2, 1, 0, 2], dtype=torch.int32)
    assert cena._indice_local(v, 6).numpy().tolist() == [0, 0, 1, 0, 1, 2]
    assert cena._indice_local(3, 4).numpy().tolist() == [0, 1, 2, 3]

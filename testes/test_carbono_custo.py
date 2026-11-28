"""Validação do módulo carbono_custo (4d) por conta à mão.

As contas usam os números da tabela escritos aqui à mão (não as funções do módulo): camadas ×
densidade × fator do material (ICE v3.0) × áreas da caixa; aço pela massa do módulo estrutura
(validada contra o Karamba na 2b) × fator; operação pela fórmula do valor presente.
"""
import json

import pytest
import torch

import otimiza.modulos.carbono_custo  # noqa: F401  (registra o módulo)
import otimiza.modulos.estrutura  # noqa: F401
from otimiza.modulos.carbono_custo.fatores import fatores_de, padrao, por_m2
from otimiza.modulos.estrutura.prototipos import TUBOS, trelica_pratt_de
from otimiza.modulos.termico import rc
from otimiza.nucleo.cache import Cache
from otimiza.nucleo.cena import CODIGO, CenaLote, Variante
from otimiza.nucleo.clima import ler_epw
from otimiza.nucleo.contrato import modulo
from otimiza.nucleo.geometria import retangulo
from otimiza.nucleo.motor import avaliar
from otimiza.nucleo.prototipos import predio
from otimiza.servidor import protocolo as pr
from otimiza.servidor.servico import Servico, metricas
from testes.conftest import EPWS_REAIS

# kgCO2e/m² à mão (espessura × densidade × fator)
PAREDE = 0.1 * 1920 * 0.213 + 0.1 * 1280 * 0.28 + 0.05 * 43 * 3.29 + 0.0127 * 800 * 0.39   # 87,77
VIDRO = 2 * 0.006 * 2500 * 1.44                                                            # 43,2
COBERTURA = 0.01 * 1120 * 0.43 + 0.05 * 43 * 3.29 + 0.1 * 1280 * 0.28 + 0.02 * 368 * 1.28  # 57,15
PISO_SOLO = 0.05 * 43 * 3.29 + 0.2 * 2240 * 0.103                                          # 53,22
PISO_INTERNO = 0.02 * 368 * 1.28 + 0.1 * 1280 * 0.28                                       # 45,26
ANUIDADE_50_4 = 21.482185   # Σ_{t=1..50} 1,04^−t (tabela de matemática financeira)
TOL = 1e-9


def _cena(variantes=(), estruturas=None, opcoes=None):
    return CenaLote(variantes=list(variantes), clima=None, estruturas=estruturas, device="cpu",
                    opcoes=opcoes or {})


def _avaliar(cena, mets, consumo=None):
    cache = Cache()
    if consumo is not None:
        cache.resultados["termico.consumo"] = torch.tensor(consumo, dtype=torch.float64)
    return modulo("carbono_custo").avaliar(cena, cache, set(mets))


def test_camadas_iguais_as_do_termico():
    """A tabela usa as mesmas camadas (espessura, densidade) das CONSTRUCOES do RC."""
    f = padrao()
    for nome, c in rc.CONSTRUCOES.items():
        assert [(d, rho) for d, _, rho, _ in c["camadas"]] == \
            [(esp, rho) for _, esp, rho in f["construcoes"][nome]], nome
    for nome, valor in (("parede", PAREDE), ("vidro", VIDRO), ("cobertura", COBERTURA),
                        ("piso_solo", PISO_SOLO), ("piso_interno", PISO_INTERNO)):
        assert por_m2(f, nome)[1] == pytest.approx(valor, rel=TOL), nome


def test_caixa_conta_a_mao():
    """Sala 6×8×3, janela N 40%: parede 84 − 7,2 m², vidro 7,2, cobertura 48, piso 48."""
    r = _avaliar(_cena([predio(), predio(rotacao_graus=30)]),
                 {"carbono_incorporado", "carbono_incorporado_m2", "custo_construcao",
                  "custo_construcao_m2"})
    co2 = 76.8 * PAREDE + 7.2 * VIDRO + 48 * COBERTURA + 48 * PISO_SOLO
    custo = 76.8 * 250 + 7.2 * 1200 + 48 * 350 + 48 * 200
    for i in range(2):
        assert r["carbono_incorporado"][i].item() == pytest.approx(co2, rel=TOL)
        assert r["carbono_incorporado_m2"][i].item() == pytest.approx(co2 / 48, rel=TOL)
        assert r["custo_construcao"][i].item() == pytest.approx(custo, rel=TOL)
        assert r["custo_construcao_m2"][i].item() == pytest.approx(custo / 48, rel=TOL)


def test_laje_interna_conta_uma_vez():
    """Um piso a meia altura (laje) soma 48 m² × piso interno, e a área de piso dobra."""
    v = predio()
    lv, lf = retangulo(0, 0, 6, 8, z=1.5)
    n = v.vertices.shape[0]
    v2 = Variante(torch.cat([v.vertices, lv]), torch.cat([v.faces, lf + n]),
                  torch.cat([v.rotulos, torch.full((2,), CODIGO["piso"])]),
                  torch.cat([v.refletancia, torch.full((2,), 0.2)]),
                  torch.cat([v.transmitancia, torch.zeros(2)]))
    r = _avaliar(_cena([v, v2]), {"carbono_incorporado", "carbono_incorporado_m2"})
    assert (r["carbono_incorporado"][1] - r["carbono_incorporado"][0]).item() == \
        pytest.approx(48 * PISO_INTERNO, rel=TOL)
    assert r["carbono_incorporado_m2"][1].item() == pytest.approx(r["carbono_incorporado"][1].item() / 96,
                                                                    rel=TOL)


def test_trelica_massa_vezes_fator():
    """Treliça Pratt: aço = massa do módulo estrutura (= Karamba) × 1,55 kgCO2e/kg e × R$ 18/kg;
    sem malha não há área de piso (por m² = NaN)."""
    ps = [dict(altura=1.0, n_paineis=4, secao="RO114.3/4"),
          dict(altura=2.0, n_paineis=8, secao=list(TUBOS)[-1])]
    ests = [trelica_pratt_de(p) for p in ps]
    cena = _cena(estruturas=ests)
    massa = avaliar(cena, ["estrutura.massa"], "rapido")["estrutura.massa"].double().cpu()
    r = _avaliar(cena, {"carbono_incorporado", "custo_construcao", "carbono_incorporado_m2"})
    assert torch.allclose(r["carbono_incorporado"], massa * 1.55, rtol=1e-6)
    assert torch.allclose(r["custo_construcao"], massa * 18, rtol=1e-6)
    assert torch.isnan(r["carbono_incorporado_m2"]).all()
    assert massa[1] > massa[0] > 0


def test_operacao_e_ciclo_de_vida():
    """Consumo dado (kWh/ano): 0,0385 kgCO2e/kWh, R$ 0,85/kWh, 50 anos a 4%."""
    consumo = [5000.0, 12000.0]
    r = _avaliar(_cena([predio(), predio()]), set(modulo("carbono_custo").metricas), consumo)
    co2 = 76.8 * PAREDE + 7.2 * VIDRO + 48 * COBERTURA + 48 * PISO_SOLO
    custo = 76.8 * 250 + 7.2 * 1200 + 48 * 350 + 48 * 200
    for i, c in enumerate(consumo):
        assert r["carbono_operacional"][i].item() == pytest.approx(c * 0.0385, rel=TOL)
        assert r["carbono_total"][i].item() == pytest.approx(co2 + 50 * c * 0.0385, rel=TOL)
        assert r["custo_operacional"][i].item() == pytest.approx(c * 0.85, rel=TOL)
        assert r["custo_ciclo_vida"][i].item() == pytest.approx(custo + ANUIDADE_50_4 * c * 0.85,
                                                                 rel=1e-6)
    # sem desconto: ciclo de vida = construção + N × operação
    r = _avaliar(_cena([predio()], opcoes={"carbono_custo": {"taxa_desconto": 0, "anos": 10}}),
                 {"custo_ciclo_vida", "carbono_total"}, [1000.0])
    assert r["custo_ciclo_vida"][0].item() == pytest.approx(custo + 10 * 850, rel=TOL)
    assert r["carbono_total"][0].item() == pytest.approx(co2 + 10 * 38.5, rel=TOL)


def test_trocas_do_usuario(tmp_path):
    arq = tmp_path / "meus.json"
    arq.write_text(json.dumps({"materiais": {"aco": {"kgco2e_kg": 1.0}, "madeira": {"kgco2e_kg": 0.5}},
                               "construcoes": {"parede": [["madeira", 0.1, 500]]},
                               "custo": {"parede": 100}}), encoding="utf-8")
    f = fatores_de({"arquivo": str(arq), "materiais": {"aco": {"kgco2e_kg": 1.2}}, "anos": 30})
    assert f["materiais"]["aco"]["kgco2e_kg"] == 1.2          # o dicionário vence o arquivo
    assert f["anos"] == 30 and f["custo"]["parede"] == 100
    assert por_m2(f, "parede") == pytest.approx((50.0, 25.0))
    assert f["materiais"]["vidro"]["kgco2e_kg"] == 1.44       # o resto continua o padrão
    r = _avaliar(_cena([predio()], opcoes={"carbono_custo": {"arquivo": str(arq)}}),
                 {"carbono_incorporado"})
    assert r["carbono_incorporado"][0].item() == pytest.approx(
        76.8 * 25 + 7.2 * VIDRO + 48 * COBERTURA + 48 * PISO_SOLO, rel=TOL)
    for ruim in ({"tarifa": 1}, {"energia": {"tarif": 1}}, {"arquivo": str(tmp_path / "nao.json")},
                 {"construcoes": {"parede": [["inexistente", 0.1, 1]]}}, {"anos": -1}):
        with pytest.raises(ValueError):
            fatores_de(ruim)
    assert fatores_de(None) == fatores_de({})


def test_incorporado_nao_roda_o_termico():
    """`precisa`: só as métricas de operação pedem termico.consumo."""
    cena = _cena([predio()])
    cache = Cache()
    avaliar(cena, ["carbono_custo.carbono_incorporado"], "rapido", cache)
    assert not any(k.startswith("termico.") or k.startswith("sol_luz.") for k in cache.resultados)


@pytest.mark.skipif(not EPWS_REAIS, reason="sem EPW real em data/epw")
def test_operacional_pelo_motor_com_termico():
    clima = ler_epw(EPWS_REAIS[0])
    cena = CenaLote([predio(), predio(janelas={"N": 0.8, "O": 0.5})], clima)
    r = avaliar(cena, ["carbono_custo.carbono_operacional", "termico.consumo"], "rapido")
    assert torch.allclose(r["carbono_custo.carbono_operacional"].double().cpu(),
                          r["termico.consumo"].double().cpu() * 0.0385, rtol=1e-12)
    assert (r["termico.consumo"] > 0).all()


def test_servidor_metricas_e_campo_carbono_custo():
    assert set(metricas()["carbono_custo"]) == set(modulo("carbono_custo").metricas)
    s = Servico()
    try:
        g = {"estrutura": pr.estrutura_para_dict(trelica_pratt_de(dict(altura=1.0, n_paineis=4,
                                                                         secao="RO114.3/4")))}
        g["estrutura"]["secao"] = "RO114.3/4"
        ped = dict(versao=1, geometrias=[g], metricas=["carbono_custo.carbono_incorporado",
                                                         "estrutura.massa"], epw=None)
        # sem EPW: as métricas de construção não carregam o clima
        a = s.avaliar(dict(ped, epw="C:/nao/existe.epw"))["valores"]
        assert a["carbono_custo.carbono_incorporado"][0] == pytest.approx(
            a["estrutura.massa"][0] * 1.55, rel=1e-6)
        b = s.avaliar(dict(ped, carbono_custo={"materiais": {"aco": {"kgco2e_kg": 1.0}}}))["valores"]
        assert b["carbono_custo.carbono_incorporado"][0] == pytest.approx(a["estrutura.massa"][0], rel=1e-6)
        with pytest.raises(pr.ErroProtocolo):
            s.avaliar(dict(ped, carbono_custo={"tarifa": 1}))
    finally:
        s.encerrar()

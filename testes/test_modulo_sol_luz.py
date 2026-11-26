"""1f: contrato de módulo, motor de problemas e módulo sol_luz."""
import time

import pytest
import torch

import otimiza.modulos.sol_luz  # noqa: F401  (registra o módulo)
from otimiza.nucleo.cache import Cache
from otimiza.nucleo.cena import CenaLote, media_por_variante
from otimiza.nucleo.clima import ler_epw
from otimiza.nucleo.contrato import Metrica, Modulo, modulo, registrar, registrados
from otimiza.nucleo.geometria import caixa, retangulo
from otimiza.nucleo.motor import Problema, avaliar, avaliar_problema, ordem_de_execucao, separar
from otimiza.nucleo.prototipos import predio
from testes.conftest import EPWS_REAIS

precisa_epw = pytest.mark.skipif(not EPWS_REAIS, reason="nenhum EPW real em data/epw")


# ---------------------------------------------------------------- contrato
class _A(Modulo):
    nome = "_teste_a"
    metricas = {"x": Metrica("-", "min")}

    def avaliar(self, cena, cache, metricas, nivel="padrao"):
        return {"x": torch.arange(cena.n, dtype=torch.float32)}


class _B(Modulo):
    nome = "_teste_b"
    depende_de = ("_teste_a",)
    metricas = {"dobro": Metrica("-", "max")}

    def avaliar(self, cena, cache, metricas, nivel="padrao"):
        return {"dobro": 2 * cache.resultados["_teste_a.x"]}


class _CenaFalsa:
    n = 3


def test_contrato_e_dependencias():
    registrar(_A())
    registrar(_B())
    assert ordem_de_execucao({"_teste_b"}) == ["_teste_a", "_teste_b"]
    r = avaliar(_CenaFalsa(), ["_teste_b.dobro"])
    assert r["_teste_b.dobro"].tolist() == [0, 2, 4]
    with pytest.raises(KeyError):
        avaliar(_CenaFalsa(), ["_teste_b.nao_existe"])
    with pytest.raises(ValueError):
        separar("sem_ponto")
    with pytest.raises(ValueError):
        Metrica("-", "maximizar")
    assert "sol_luz" in registrados()


def test_problema_sentidos_e_restricoes():
    registrar(_A())
    registrar(_B())
    p = Problema(objetivos=["_teste_a.x", "_teste_b.dobro"], restricoes=[("_teste_a.x", ">=", 1)])
    f, viol = avaliar_problema(_CenaFalsa(), p)
    assert f[:, 0].tolist() == [0, 1, 2]       # min: igual
    assert f[:, 1].tolist() == [0, -2, -4]     # max: vira −valor
    assert viol.tolist() == [1, 0, 0]


def test_media_por_variante():
    v = torch.tensor([1.0, 3.0, 10.0])
    var = torch.tensor([0, 0, 2])
    m = media_por_variante(v, var, 3, torch.tensor([1.0, 3.0, 1.0]))
    assert m[0].item() == pytest.approx(2.5)
    assert torch.isnan(m[1])
    assert m[2].item() == 10


# ---------------------------------------------------------------- sol_luz
@pytest.fixture(scope="module")
def clima():
    return ler_epw(EPWS_REAIS[0]) if EPWS_REAIS else None


@precisa_epw
def test_metricas_por_variante_e_coerencia(clima):
    torre = caixa(-10, 14, 0, 16, 24, 30)  # ao norte do prédio
    piso_viz = retangulo(-10, 9, 16, 13.5)  # entorno sensível entre prédio e torre
    variantes = [
        predio(janelas={"N": 0.2}),
        predio(janelas={"N": 0.6}),
        predio(janelas={"N": 0.6}, contexto=[torre]),
        predio(janelas={"N": 0.6}, entorno=[piso_viz]),
        predio(altura=12.0, janelas={"N": 0.6}, entorno=[piso_viz]),
    ]
    cena = CenaLote(variantes, clima)
    cache = Cache()
    chaves = [f"sol_luz.{m}" for m in modulo("sol_luz").metricas]
    r = avaliar(cena, chaves, nivel="rapido", cache=cache)
    for k, v in r.items():
        assert v.shape == (5,), k
    da, df, ase = r["sol_luz.da"], r["sol_luz.df"], r["sol_luz.ase"]
    assert df[1] > df[0] and da[1] >= da[0] and ase[1] >= ase[0]   # mais janela, mais luz
    assert df[2] < df[1]                                            # torre ao norte tira luz
    assert r["sol_luz.radiacao_fachada"][2] < r["sol_luz.radiacao_fachada"][1]
    perda = r["sol_luz.perda_sol_entorno"]
    assert torch.isnan(perda[0]) and perda[4] > perda[3] >= 0        # prédio mais alto: mais sombra
    assert r["sol_luz.fv"][4].item() == pytest.approx(r["sol_luz.fv"][3].item(), rel=0.02)  # mesma cobertura
    assert (r["sol_luz.ga"] >= 0).all() and (r["sol_luz.ga"] <= 100).all()


@precisa_epw
def test_so_calcula_o_necessario(clima):
    cena = CenaLote([predio()], clima)
    cache = Cache()
    avaliar(cena, ["sol_luz.radiacao_fachada"], nivel="rapido", cache=cache)
    chaves = {k[0] for k in cache._dados}
    assert "matriz" in chaves and "k_dir" not in chaves  # não montou o céu visível
    faltas = cache.faltas
    avaliar(cena, ["sol_luz.radiacao_fachada"], nivel="rapido", cache=cache)
    assert cache.faltas == faltas  # segunda vez: tudo do cache


@precisa_epw
@pytest.mark.parametrize("nivel", ["rapido", "padrao"])
def test_benchmark_variantes_por_segundo(clima, nivel, capsys):
    torre = caixa(-10, 14, 0, 16, 24, 30)
    g = torch.Generator().manual_seed(0)
    variantes = []
    for _ in range(64):
        jan = {k: float(torch.rand(1, generator=g)) * 0.6 for k in "NLSO"}
        variantes.append(predio(largura=4 + 6 * float(torch.rand(1, generator=g)),
                                profundidade=6 + 6 * float(torch.rand(1, generator=g)),
                                janelas=jan, rotacao_graus=float(torch.rand(1, generator=g)) * 360,
                                contexto=[torre]))
    cena = CenaLote(variantes, clima)
    cache = Cache()
    avaliar(CenaLote(variantes[:2], clima), ["sol_luz.da"], nivel=nivel, cache=cache)  # aquece
    tempos = {}
    for grupo in (["radiacao_fachada", "fv"], ["da", "sda", "udi", "ase", "df"], ["medi_horas"], ["ga"]):
        torch.cuda.synchronize()
        t0 = time.perf_counter()
        r = avaliar(cena, [f"sol_luz.{m}" for m in grupo], nivel=nivel, cache=cache)
        torch.cuda.synchronize()
        tempos["+".join(grupo)] = time.perf_counter() - t0
    with capsys.disabled():
        print(f"\nnível {nivel}, 64 variantes:")
        for k, t in tempos.items():
            print(f"   {k:28s} {t:6.2f} s  ({64 / t:7.1f} variantes/s)")
        total = sum(tempos.values())
        print(f"   {'tudo':28s} {total:6.2f} s  ({64 / total:7.1f} variantes/s)")
    assert all(torch.isfinite(v).all() for v in r.values())

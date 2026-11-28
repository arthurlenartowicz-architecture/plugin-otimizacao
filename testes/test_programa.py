"""Validação do módulo programa (4e) por conta à mão: caixa num lote (CA, TO, altura, recuos),
caixa girada e com balanço (TO pela grade × área exata), saída do lote, e uma planta com
ambientes de áreas, paredes e centros conhecidos (atendimento, desvio, eficiência, adjacência,
distância)."""
import json
import math

import pytest
import torch

import otimiza.modulos.programa  # noqa: F401  (registra o módulo)
from otimiza.modulos.programa.modulo import ambientes_de
from otimiza.modulos.programa.necessidades import programa_de
from otimiza.nucleo.cache import Cache
from otimiza.nucleo.cena import CODIGO, CenaLote, Variante
from otimiza.nucleo.contrato import modulo
from otimiza.nucleo.geometria import retangulo
from otimiza.nucleo.motor import avaliar
from otimiza.nucleo.prototipos import predio
from otimiza.servidor import protocolo as pr
from otimiza.servidor.servico import Servico, metricas

LOTE = [[0, 0], [20, 0], [20, 30], [0, 30]]      # 600 m²
URB = {"area_construida", "ca", "taxa_ocupacao", "altura", "recuo_min"}


def _avaliar(variantes, programa, mets, nivel="validacao"):
    cena = CenaLote(list(variantes), None, device="cpu", opcoes={"programa": programa})
    return modulo("programa").avaliar(cena, Cache(), set(mets), nivel)


def _com(v: Variante, *extras):
    """Variante + malhas extras [(vértices, faces, rótulo)]."""
    vs, fs, rot = [v.vertices], [v.faces], [v.rotulos]
    n = v.vertices.shape[0]
    for vv, ff, r in extras:
        vs.append(vv); fs.append(ff + n); rot.append(torch.full((ff.shape[0],), CODIGO[r]))
        n += vv.shape[0]
    f = torch.cat(fs)
    return Variante(torch.cat(vs), f, torch.cat(rot), torch.full((f.shape[0],), 0.5),
                    torch.zeros(f.shape[0]))


def test_caixa_no_lote():
    """Caixa 6×8×3 em (5, 10): CA 48/600, TO 8%, altura 3, recuos 5 / 9 / 10 / 12 → 5."""
    r = _avaliar([predio(origem=(5, 10))], {"lote": LOTE}, URB)
    assert r["area_construida"][0].item() == pytest.approx(48)
    assert r["ca"][0].item() == pytest.approx(0.08)
    assert r["taxa_ocupacao"][0].item() == pytest.approx(8.0, rel=1e-9)     # grade alinhada: exata
    assert r["altura"][0].item() == pytest.approx(3.0)
    assert r["recuo_min"][0].item() == pytest.approx(5.0)
    # lote com cota (z = −1): altura conta a partir do terreno
    r = _avaliar([predio(origem=(5, 10))], {"lote": [p + [-1] for p in LOTE]}, {"altura"})
    assert r["altura"][0].item() == pytest.approx(4.0)


@pytest.mark.parametrize("nivel,tol", [("rapido", 0.03), ("padrao", 0.01), ("validacao", 0.004)])
def test_caixa_girada(nivel, tol):
    """Girada 30°: a projeção continua 48 m²; recuo = menor distância dos cantos às divisas."""
    o, a = (8.0, 10.0), math.radians(30)
    cantos = [(o[0] + x * math.cos(a) - y * math.sin(a), o[1] + x * math.sin(a) + y * math.cos(a))
              for x, y in ((0, 0), (6, 0), (6, 8), (0, 8))]
    recuo = min(min(x, 20 - x, y, 30 - y) for x, y in cantos)
    r = _avaliar([predio(origem=o, rotacao_graus=30)], {"lote": LOTE}, URB, nivel)
    assert r["taxa_ocupacao"][0].item() == pytest.approx(8.0, rel=tol)
    assert r["recuo_min"][0].item() == pytest.approx(recuo, abs=1e-9)
    assert r["area_construida"][0].item() == pytest.approx(48)


def test_balanco_e_fora_do_lote():
    """Cobertura em balanço de 2 m a oeste: projeção 48 + 16 m², recuo 5 → 3. Caixa em x = −2:
    recuo −2 (o canto mais longe para fora)."""
    v = _com(predio(origem=(5, 10)), (*retangulo(3, 10, 5, 18, z=3.0), "cobertura"))
    r = _avaliar([v, predio(origem=(-2, 10))], {"lote": LOTE}, {"taxa_ocupacao", "recuo_min", "ca"})
    assert r["taxa_ocupacao"][0].item() == pytest.approx(100 * 64 / 600, rel=1e-9)
    assert r["recuo_min"][0].item() == pytest.approx(3.0)
    assert r["recuo_min"][1].item() == pytest.approx(-2.0)
    assert r["ca"][0].item() == pytest.approx(0.08)           # balanço não é piso


def _planta():
    """Sala 4×5 (feita de dois retângulos com vértices repetidos na emenda), cozinha 2,85×5 a
    15 cm (parede) a leste, quarto 4×2,85 a 15 cm ao norte, circulação sem nome 0,85×8."""
    pisos = [(retangulo(0, 0, 2, 5), "sala"), (retangulo(2, 0, 4, 5), "sala"),
             (retangulo(4.15, 0, 7, 5), "cozinha"), (retangulo(0, 5.15, 4, 8), "quarto"),
             (retangulo(7.15, 0, 8, 8), None)]
    usos = ["sala", "cozinha", "quarto"]
    vs, fs, amb, n = [], [], [], 0
    for (v, f), uso in pisos:
        vs.append(v); fs.append(f + n); n += v.shape[0]
        amb += [usos.index(uso) if uso else -1] * f.shape[0]
    f = torch.cat(fs)
    return Variante(torch.cat(vs), f, torch.full((f.shape[0],), CODIGO["piso"]),
                    torch.full((f.shape[0],), 0.2), torch.zeros(f.shape[0]),
                    ambientes=torch.tensor(amb), usos=tuple(usos))


PROG = {"areas": {"sala": 20, "cozinha": 12, "quarto": 12, "banheiro": 4},
        "adjacencias": [["sala", "cozinha", 2], "sala-quarto", ["cozinha", "quarto"], "quarto-banheiro:1"]}
PROG_METS = {"atendimento_programa", "desvio_programa", "eficiencia_planta", "adjacencia",
             "distancia_ponderada", "area_construida"}


def test_planta_com_ambientes():
    v = _planta()
    amb = ambientes_de(v)
    sala = next(a for a in amb if a["uso"] == "sala")
    borda = (sala["bordas"][:, 1] - sala["bordas"][:, 0]).norm(dim=1).sum().item()
    assert borda == pytest.approx(18.0)        # perímetro 4×5: emenda e diagonais não são borda
    r = _avaliar([v], PROG, PROG_METS)
    a_sala, a_coz, a_qto, a_circ = 20.0, 2.85 * 5, 4 * 2.85, 0.85 * 8
    assert r["area_construida"][0].item() == pytest.approx(a_sala + a_coz + a_qto + a_circ)
    assert r["atendimento_programa"][0].item() == pytest.approx(100 * (20 + 12 + a_qto + 0) / 48)
    assert r["desvio_programa"][0].item() == pytest.approx(100 * (0 + (a_coz - 12) + (12 - a_qto) + 4) / 48)
    assert r["eficiencia_planta"][0].item() == pytest.approx(
        100 * (a_sala + a_coz + a_qto) / (a_sala + a_coz + a_qto + a_circ))
    # sala–cozinha (5 m de parede) e sala–quarto (4 m) sim; cozinha–quarto só se tocam na quina;
    # banheiro não existe: 3 de 5 pesos
    assert r["adjacencia"][0].item() == pytest.approx(60.0)
    sc, sq = 5.575 - 2, 6.575 - 2.5
    cq = math.hypot(5.575 - 2, 6.575 - 2.5)
    assert r["distancia_ponderada"][0].item() == pytest.approx((2 * sc + sq + cq) / 4)
    # parede mais grossa que o limite → nada adjacente; porta exige 4,5 m → só sala–cozinha
    r = _avaliar([v], {**PROG, "adjacencia": {"parede_max": 0.1}}, {"adjacencia"})
    assert r["adjacencia"][0].item() == pytest.approx(0.0)
    r = _avaliar([v], {**PROG, "adjacencia": {"comprimento_min": 4.5}}, {"adjacencia"})
    assert r["adjacencia"][0].item() == pytest.approx(40.0)


def test_lote_igual_individual_e_sem_ambientes():
    vs = [_planta(), predio(origem=(5, 10)), _planta()]
    prog = {**PROG, "lote": LOTE}
    todos = _avaliar(vs, prog, PROG_METS | URB)
    for i, v in enumerate(vs):
        um = _avaliar([v], prog, PROG_METS | URB)
        for k in um:
            assert torch.allclose(todos[k][i:i + 1], um[k], equal_nan=True), k
    # sem ambientes: nada atendido, adjacência 0, distância indefinida
    assert todos["atendimento_programa"][1].item() == 0
    assert todos["adjacencia"][1].item() == 0
    assert math.isnan(todos["distancia_ponderada"][1].item())


def test_programa_json_e_erros(tmp_path):
    arq = tmp_path / "programa.json"
    arq.write_text(json.dumps({"lote": LOTE + [LOTE[0]], "areas": {"sala": 30},
                               "adjacencia": {"parede_max": 0.3}}), encoding="utf-8")
    p = programa_de({"arquivo": str(arq), "areas": {"sala": 20, "quarto": 12},
                     "adjacencia": {"comprimento_min": 1.0}})
    assert p["area_lote"] == pytest.approx(600)          # ponto repetido de curva fechada sai
    assert len(p["lote"]) == 4 and p["z_lote"] is None
    assert p["areas"] == {"sala": 20, "quarto": 12}       # o dicionário troca as áreas inteiras
    assert p["adjacencia"] == {"parede_max": 0.3, "comprimento_min": 1.0}
    assert programa_de(None)["lote"] is None
    for ruim in ({"lot": LOTE}, {"lote": [[0, 0], [1, 1], [2, 2]]}, {"lote": [[0, 0], [1, 0]]},
                 {"areas": {"sala": 0}}, {"adjacencias": ["sala"]}, {"adjacencias": [["a", "a"]]},
                 {"adjacencias": ["a-b:-1"]}, {"adjacencia": {"parede": 1}},
                 {"arquivo": str(tmp_path / "nao.json")}):
        with pytest.raises(ValueError):
            programa_de(ruim)
    with pytest.raises(ValueError, match="lote"):
        _avaliar([predio()], {}, {"ca"})


def test_protocolo_e_servidor():
    """Pisos com 'ambiente' pelo protocolo (ida e volta) e o campo 'programa' no /avaliar, sem EPW."""
    v = _planta()
    malhas = pr.malhas_de_variante(v)
    assert {m.get("ambiente") for m in malhas["piso"]} == {"sala", "cozinha", "quarto", None}
    v2 = pr.variante_de(malhas)
    assert sorted(v2.usos) == ["cozinha", "quarto", "sala"]
    um, dois = _avaliar([v], PROG, PROG_METS), _avaliar([v2], PROG, PROG_METS)
    for k in um:
        assert dois[k].item() == pytest.approx(um[k].item(), rel=1e-6), k
    with pytest.raises(pr.ErroProtocolo):
        pr.variante_de({"fachada": dict(malhas["piso"][0], ambiente="sala")})

    assert set(metricas()["programa"]) == set(modulo("programa").metricas)
    s = Servico()
    try:
        ped = dict(versao=1, geometrias=[{"malhas": pr.malhas_de_variante(predio(origem=(5, 10)))}],
                   metricas=["programa.ca", "programa.taxa_ocupacao", "programa.recuo_min"],
                   epw="C:/nao/existe.epw", nivel="padrao", programa={"lote": LOTE})
        a = s.avaliar(ped)["valores"]
        assert a["programa.ca"][0] == pytest.approx(0.08)
        assert a["programa.taxa_ocupacao"][0] == pytest.approx(8.0, rel=1e-6)
        assert a["programa.recuo_min"][0] == pytest.approx(5.0, rel=1e-6)
        for ruim in (dict(ped, programa=None), dict(ped, programa={"lot": LOTE})):
            with pytest.raises(pr.ErroProtocolo):
                s.avaliar(ruim)
    finally:
        s.encerrar()


def test_motor_nao_roda_outros_modulos():
    cache = Cache()
    cena = CenaLote([predio()], None, device="cpu", opcoes={"programa": {"lote": LOTE}})
    avaliar(cena, ["programa.ca", "programa.altura"], "rapido", cache)
    assert all(k.startswith("programa.") for k in cache.resultados)

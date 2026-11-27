"""Servidor (GH gera, Python avalia): protocolo e ponta a ponta com clientes falsos.

O cliente falso faz o papel do Grasshopper: pega tarefas, monta a geometria com os
protótipos em Python e devolve no formato do protocolo (base64, por rótulo).
"""
import json
import threading
import time
import urllib.request

import numpy as np
import pytest
import torch

from otimiza.modulos.estrutura.prototipos import TUBOS, trelica_pratt, trelica_pratt_de
from otimiza.nucleo.cena import CODIGO, ROTULOS
from otimiza.nucleo.geometria import caixa
from otimiza.nucleo.motor import Problema, avaliar
from otimiza.nucleo.parametros import Continuo, Espaco, Inteiro, cena_de
from otimiza.nucleo.prototipos import predio_de
from otimiza.servidor import protocolo as pr
from otimiza.servidor.rede import criar
from otimiza.servidor.servico import Servico, esperar
from testes.conftest import EPWS_REAIS


# ---------------------------------------------------------------- protocolo
def test_problema_de_texto():
    p = pr.problema_de("max sol_luz.udi\n# comentário\nsol_luz.ase\n\nsol_luz.da >= 50\n")
    assert p.objetivos == ["sol_luz.udi", "sol_luz.ase"]
    assert p.sentidos == {"sol_luz.udi": "max"}
    assert p.restricoes == [("sol_luz.da", ">=", 50.0)]
    # Panel multilinha do GH: um item com várias linhas (\r\n)
    q = pr.problema_de(["max sol_luz.da\r\nmax sol_luz.udi\r\nmin sol_luz.ase"])
    assert q.objetivos == ["sol_luz.da", "sol_luz.udi", "sol_luz.ase"]
    assert q.sentidos["sol_luz.ase"] == "min"
    with pytest.raises(pr.ErroProtocolo):
        pr.problema_de("sol_luz.da > 50")
    with pytest.raises(pr.ErroProtocolo):
        pr.problema_de("sol_luz.da <= 5")        # só restrição, sem objetivo


def test_espaco_e_casas_do_slider():
    esp = pr.espaco_de([dict(nome="a", tipo="continuo", minimo=0, maximo=1, casas=2),
                        dict(nome="n", tipo="inteiro", minimo=2, maximo=5),
                        dict(nome="c", tipo="categorico", opcoes=["x", "y"])])
    p = esp.decodificar([[0.12345, 0.0, 0.9]])[0]
    assert p == {"a": 0.12, "n": 2, "c": "y"}
    U = esp.arredondar(np.array([[0.121, 0.1, 0.9], [0.124, 0.2, 0.8]]))
    assert np.allclose(U[0], U[1])               # mesmo valor no slider → mesmo vetor
    assert esp.decodificar(U)[0]["a"] == 0.12


def test_array_base64_e_listas():
    a = np.arange(12, dtype=float).reshape(4, 3)
    assert np.array_equal(pr.array(pr.para_b64(a, "f4"), "f4", 3), a)
    assert np.array_equal(pr.array(a.ravel().tolist(), "f8", 3), a)
    with pytest.raises(pr.ErroProtocolo):
        pr.array([1, 2], "f8", 3)


def test_variante_por_rotulo_e_escala():
    v = predio_de({"janela_N": 0.4, "janela_L": 0.2}, contexto=[caixa(-10, 14, 0, 16, 24, 30)])
    m = pr.malhas_de_variante(v)
    w = pr.variante_de(m)
    for r in ROTULOS:      # mesma área por rótulo
        assert _area(w, r) == pytest.approx(_area(v, r), rel=1e-9, abs=1e-12)
    # em milímetros, com escala 0,001
    mm = {r: {"vertices": (np.array(pr.array(d["vertices"], "f8", 3)) * 1000).ravel().tolist(),
              "faces": d["faces"]} for r, d in m.items()}
    w_mm = pr.variante_de(mm, escala=0.001)
    assert torch.allclose(w_mm.vertices, w.vertices, atol=1e-9)
    # materiais por rótulo
    w2 = pr.variante_de(m, materiais={"piso": {"rho": 0.3}, "vidro": {"tau": 0.5}})
    assert torch.allclose(w2.refletancia[w2.rotulos == CODIGO["piso"]], torch.tensor(0.3))
    assert torch.allclose(w2.transmitancia[w2.rotulos == CODIGO["vidro"]], torch.tensor(0.5))
    with pytest.raises(pr.ErroProtocolo):
        pr.variante_de({"telhado": m["cobertura"]})


def _area(v, rotulo):
    f = v.faces[v.rotulos == CODIGO[rotulo]]
    p = v.vertices.double()
    return float(torch.linalg.cross(p[f[:, 1]] - p[f[:, 0]], p[f[:, 2]] - p[f[:, 0]]).norm(dim=1).sum() / 2)


def test_estrutura_por_linhas_igual_ao_prototipo():
    """Linhas + pontos de apoio/carga (como o GH manda) → mesmo FEM do protótipo."""
    import otimiza.modulos.estrutura  # noqa: F401
    from otimiza.nucleo.cena import CenaLote
    s = trelica_pratt(altura=1.7, n_paineis=6, secao=TUBOS["RO88.9/4"])
    d = pr.estrutura_para_dict(s)
    d["secao"] = "RO88.9/4"
    e = pr.estrutura_de(d)
    assert e.n_nos == s.n_nos and e.n_barras == s.n_barras
    chaves = ["estrutura.massa", "estrutura.deslocamento_max", "estrutura.utilizacao"]
    a = avaliar(CenaLote(variantes=[], clima=None, estruturas=[s]), chaves)
    b = avaliar(CenaLote(variantes=[], clima=None, estruturas=[e]), chaves)
    for k in chaves:
        assert float(b[k]) == pytest.approx(float(a[k]), rel=1e-10)
    # em cm (escala 0,01) dá o mesmo
    d_cm = dict(d, barras=(np.array(d["barras"]) * 100).tolist(),
                apoios=[dict(g, pontos=(np.array(g["pontos"]) * 100).tolist()) for g in d["apoios"]],
                cargas=[dict(g, pontos=(np.array(g["pontos"]) * 100).tolist()) for g in d["cargas"]])
    assert torch.allclose(pr.estrutura_de(d_cm, escala=0.01).nos, e.nos)


# ---------------------------------------------------------------- cliente falso
ESPACO_TRELICA = [dict(nome="altura", tipo="continuo", minimo=0.6, maximo=3.0, casas=2),
                  dict(nome="n_paineis", tipo="inteiro", minimo=4, maximo=10),
                  dict(nome="secao", tipo="categorico", opcoes=list(TUBOS))]
PROBLEMA_TRELICA = ["estrutura.massa", "estrutura.deslocamento_max", "estrutura.utilizacao <= 100"]


def geometria_trelica(p):
    s = trelica_pratt_de(p)
    d = pr.estrutura_para_dict(s)
    d["secao"] = p["secao"]
    return {"estrutura": d}


def _post(url, rota, dados):
    req = urllib.request.Request(url + rota, json.dumps(dados).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.loads(r.read())


def _cliente(url, gerar, maximo, contagem, parar):
    while not parar.is_set():
        r = _post(url, "/tarefas", {"maximo": maximo})
        if r["estado"] in ("fim", "erro"):
            return
        if not r.get("tarefas"):
            time.sleep(0.02)
            continue
        res = []
        for t in r["tarefas"]:
            t0 = time.perf_counter()
            g = gerar(t["parametros"])
            res.append(dict(id=t["id"], geometria=g, tempo_s=time.perf_counter() - t0))
            contagem.append(t["parametros"])
        _post(url, "/resultados", {"resultados": res})


@pytest.fixture
def servidor():
    srv = criar(porta=0)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}", srv
    srv.shutdown()
    srv.server_close()


def test_http_trelica_dois_clientes(servidor, tmp_path):
    """Dois 'Rhinos' pegando tarefas da mesma fila; o resultado é o da avaliação direta."""
    url, srv = servidor
    ini = _post(url, "/iniciar", dict(parametros=ESPACO_TRELICA, problema=PROBLEMA_TRELICA,
                                      nivel="padrao", populacao=20, geracoes=6, semente=1,
                                      caso="teste", saida=str(tmp_path), cliente={"x": 1}))
    assert ini["estado"] == "gerando" and ini["algoritmo"] == "nsga2"
    contagem, parar = [], threading.Event()
    ths = [threading.Thread(target=_cliente, args=(url, geometria_trelica, 3, contagem, parar))
           for _ in range(2)]
    for t in ths:
        t.start()
    for t in ths:
        t.join(120)
    parar.set()
    with urllib.request.urlopen(url + "/estado") as r:
        est = json.loads(r.read())
    assert est["estado"] == "fim", est.get("erro")
    otim = srv.servico.otim
    res = otim.sessao.resultado()
    assert len(res.parametros) == est["avaliacoes"] == 20 * 6
    # repetidos não voltam ao cliente (cache): menos geometrias geradas que avaliações
    assert len(contagem) == len({tuple(sorted(p.items())) for p in contagem}) <= len(res.parametros)
    # mesmos valores da avaliação direta em lote (sem GH)
    import otimiza.modulos.estrutura  # noqa: F401
    from otimiza.nucleo.motor import avaliar_problema
    pb = pr.problema_de(PROBLEMA_TRELICA)
    F, v = avaliar_problema(cena_de(trelica_pratt_de, res.parametros), pb, "padrao")
    assert np.allclose(res.F, F.double().cpu().numpy(), rtol=1e-9)
    # resultado.json com a geometria de cada solução e os dados do cliente
    dados = json.loads((tmp_path / "resultado.json").read_text(encoding="utf-8"))
    assert dados["solucoes"] and dados["solucoes"][0]["geometria"]["tipo"] == "barras"
    assert dados["info"]["cliente"] == {"x": 1}
    assert dados["info"]["tempo_cliente_s_por_variante"] is not None


def test_mesmo_resultado_que_otimizar_direto(tmp_path):
    """Pelo servidor (com cache e fila) = `otimizar_problema` direto, mesma semente."""
    import otimiza.modulos.estrutura  # noqa: F401
    from otimiza.otimizacao import otimizar_problema
    from otimiza.nucleo.parametros import Categorico
    esp = Espaco([Continuo("altura", 0.6, 3.0, 2), Inteiro("n_paineis", 4, 10),
                  Categorico("secao", tuple(TUBOS))])
    pb = pr.problema_de(PROBLEMA_TRELICA)
    direto = otimizar_problema(esp, trelica_pratt_de, pb, "padrao", populacao=16, geracoes=5,
                               semente=2)
    sv = Servico()
    otim = sv.iniciar(dict(parametros=ESPACO_TRELICA, problema=PROBLEMA_TRELICA, nivel="padrao",
                           populacao=16, geracoes=5, semente=2, saida=str(tmp_path))) and sv.otim
    while True:
        r = otim.tarefas()
        if r["estado"] == "fim":
            break
        if r["estado"] == "avaliando":
            esperar(otim)
            continue
        otim.resultados([dict(id=t["id"], geometria=geometria_trelica(t["parametros"]))
                         for t in r["tarefas"]])
        esperar(otim)
    via = otim.sessao.resultado()
    assert via.parametros == direto.parametros
    assert np.allclose(via.F, direto.F, rtol=1e-9)
    sv.encerrar()


def test_erro_de_geometria_vira_inviavel(tmp_path):
    sv = Servico()
    sv.iniciar(dict(parametros=ESPACO_TRELICA, problema=PROBLEMA_TRELICA, populacao=6,
                    geracoes=1, saida=str(tmp_path)))
    otim = sv.otim
    r = otim.tarefas()
    res = [dict(id=t["id"], erro="Brep inválido") if k == 0 else
           dict(id=t["id"], geometria=geometria_trelica(t["parametros"]))
           for k, t in enumerate(r["tarefas"])]
    out = otim.resultados(res)
    assert out["erros"]
    esperar(otim)
    assert otim.estado == "fim"
    assert (otim.sessao.resultado().violacao >= 1e6).sum() == 1
    sv.encerrar()


# ---------------------------------------------------------------- sol_luz (precisa de EPW)
@pytest.mark.skipif(not EPWS_REAIS, reason="sem EPW em data/epw")
def test_sol_luz_pelo_protocolo_igual_ao_prototipo():
    """Mesma sala (predio) pelo protocolo — por rótulo, contexto em 'fixas', em mm — dá as
    mesmas métricas do sol_luz que o protótipo avaliado direto."""
    import otimiza.modulos.sol_luz  # noqa: F401
    from otimiza.nucleo.cena import CenaLote
    from otimiza.nucleo.clima import ler_epw
    torre = caixa(-10, 14, 0, 16, 24, 30)
    params = [{"janela_N": 0.5, "janela_L": 0.1, "rotacao_graus": 20.0},
              {"janela_S": 0.3, "janela_O": 0.6, "rotacao_graus": -10.0}]
    projetos = [predio_de(p, profundidade=12.0) for p in params]
    ctx = pr.malhas_de_variante(predio_de({}, contexto=[torre]))["contexto"]

    def em_mm(m):
        return {r: {"vertices": pr.para_b64(pr.array(d["vertices"], "f8", 3) * 1000, "f4"),
                    "faces": d["faces"]} for r, d in m.items()}

    geoms = [{"malhas": em_mm(pr.malhas_de_variante(v))} for v in projetos]
    chaves = ["sol_luz.radiacao_fachada", "sol_luz.radiacao_cobertura", "sol_luz.ase",
              "sol_luz.da", "sol_luz.sda", "sol_luz.udi"]
    sv = Servico()
    via = sv.avaliar(dict(geometrias=geoms, metricas=chaves, nivel="rapido", escala=0.001,
                          epw=str(EPWS_REAIS[0]), fixas={"malhas": em_mm({"contexto": ctx})}))
    sv.encerrar()
    clima = ler_epw(EPWS_REAIS[0])
    diretos = [predio_de(p, profundidade=12.0, contexto=[torre]) for p in params]
    ref = avaliar(CenaLote(variantes=diretos, clima=clima), chaves, "rapido")
    for k in chaves:
        a, b = np.array(via["valores"][k]), ref[k].double().cpu().numpy()
        print(k, a.round(3), b.round(3))
        # determinísticas (motor A, sol direto): iguais a menos do float32 em mm; DA/UDI/sDA
        # (path tracing): a amostragem depende do índice global do sensor no lote → ruído
        if k in ("sol_luz.da", "sol_luz.udi", "sol_luz.sda"):
            assert np.allclose(a, b, atol=1.0), k
        else:
            assert np.allclose(a, b, rtol=1e-3, atol=1e-3), k

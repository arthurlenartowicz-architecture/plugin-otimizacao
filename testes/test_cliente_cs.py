"""Cliente C# (rhino/plugin/Otimiza.Cliente) contra o servidor Python real, sem Rhino.

Compila e roda o rhino/plugin/Otimiza.Testes, que faz o papel do Rhino com caixas
paramétricas em mm (base64 float32/int32 por rótulo, contexto em `fixas`): /metricas,
/avaliar e uma otimização com 1 principal + 2 trabalhadores (um para no meio e devolve
tarefas). Confere volume, área e compacidade contra a conta analítica.
"""
import base64
import json
import shutil
import subprocess
import threading
from pathlib import Path

import numpy as np
import pytest

from otimiza.servidor import protocolo as pr
from otimiza.servidor.rede import criar
from testes.conftest import EPWS_REAIS

RAIZ = Path(__file__).resolve().parents[1]
PROJETO = RAIZ / "rhino" / "plugin" / "Otimiza.Testes"
DLL = RAIZ / "rhino" / "plugin" / "bin" / "testes" / "Otimiza.Testes.dll"


def _tem_sdk():
    if shutil.which("dotnet") is None:
        return False
    r = subprocess.run(["dotnet", "--list-sdks"], capture_output=True, text=True)
    return r.returncode == 0 and r.stdout.strip() != ""


pytestmark = [pytest.mark.skipif(not _tem_sdk(), reason="sem o .NET SDK"),
              pytest.mark.skipif(not EPWS_REAIS, reason="sem EPW em data/epw")]


@pytest.fixture(scope="module")
def programa():
    r = subprocess.run(["dotnet", "build", str(PROJETO), "-c", "Release", "-nologo", "-v", "q"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    return DLL


@pytest.fixture
def servidor():
    srv = criar(porta=0)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}", srv
    srv.shutdown()
    srv.server_close()


def caixa(W, D, H, vidro_sul):
    """Conta analítica da caixa sem base (a base entra na envoltória pela divergência)."""
    paredes = 2 * (W + D) * H
    vol = W * D * H
    area = paredes + 2 * W * D
    wwr = 100 * W * H / paredes if vidro_sul else 0.0
    return dict(compacidade=area / vol, volume=vol, area_envoltoria=area, wwr=wwr,
                wwr_S=100.0 if vidro_sul else 0.0)


def test_cliente_cs_contra_servidor(programa, servidor, tmp_path):
    url, srv = servidor
    rel_arq = tmp_path / "relatorio.json"
    r = subprocess.run(["dotnet", str(programa), url, str(rel_arq), str(tmp_path / "saida"),
                        str(EPWS_REAIS[0])], capture_output=True, text=True, timeout=400)
    assert r.returncode == 0, r.stdout + r.stderr
    rel = json.loads(rel_arq.read_text(encoding="utf-8"))

    # base64 float32 / int32 little-endian, igual ao do Python
    assert np.array_equal(pr.array(rel["b64_f4"], "f4", 1).ravel(),
                          np.array([0, 1.5, -2.25, 1e6, 3.14159], dtype=np.float32))
    assert np.frombuffer(base64.b64decode(rel["b64_i4"]), "<i4").tolist() == [0, 1, 2, -1, 2**31 - 1]
    assert rel["tem_compacidade"]

    # /avaliar: duas caixas em mm, contexto em fixas
    for k, (W, D, H, v) in enumerate([(6, 8, 3, False), (10, 5, 4.5, True)]):
        ref = caixa(W, D, H, v)
        for m, x in ref.items():
            assert rel["avaliar"][f"termico.{m}"][k] == pytest.approx(x, rel=1e-5, abs=1e-6), m

    # trabalhador com genoma diferente recusou entrar
    assert rel["w3"]["estado"] == "erro"
    assert any("não tem no genoma" in x for x in rel["w3"]["log"])

    # otimização: terminou, com os três clientes gerando
    p, w1, w2 = rel["principal"], rel["w1"], rel["w2"]
    print("\nprincipal", p["gerados"], "w1", w1["gerados"], "(devolveu", w1["devolvidas"], ") w2",
          w2["gerados"])
    assert p["terminou"] == "fim" and p["estado"] == "fim", p["log"]
    assert w1["estado"] == "parado" and w1["devolvidas"] > 0
    assert w2["esperou"] and w2["estado"] == "parado"
    assert w1["gerados"] > 0 and w2["gerados"] > 0 and p["gerados"] > 0
    otim = srv.servico.otim
    res = otim.sessao.resultado()
    assert len(res.parametros) == 16 * 4 == p["ultimo"]["avaliacoes"]
    # cada projeto gerado uma vez só (cache), e nenhum perdido no /devolver
    gerados = p["gerados"] + w1["gerados"] + w2["gerados"]
    unicos = {tuple(sorted(q.items())) for q in res.parametros}
    ok = {k for k in unicos if dict(k)["largura"] <= 11.5}
    assert len(ok) <= gerados <= len(unicos)
    obj = res.objetivos
    for q, f, viol in zip(res.parametros, obj, res.violacao):
        if q["largura"] > 11.5:                  # erro de propósito do cliente → inviável
            assert viol >= 1e6
            continue
        ref = caixa(q["largura"], q["profundidade"], q["altura"], q["vidro_sul"] == "sim")
        assert f[0] == pytest.approx(ref["compacidade"], rel=1e-5)
        assert f[1] == pytest.approx(ref["volume"], rel=1e-5)
        assert (viol > 0) == (ref["wwr"] > 30 + 1e-6)
        assert q["largura"] == round(q["largura"], 2) and q["altura"] == round(q["altura"], 1)
    dados = json.loads((tmp_path / "saida" / "resultado.json").read_text(encoding="utf-8"))
    assert dados["info"]["cliente"]["genoma"][0] == {"nome": "largura", "guid": "guid-largura",
                                                     "tipo": "x"}
    assert dados["info"]["tempo_cliente_s_por_variante"] is not None

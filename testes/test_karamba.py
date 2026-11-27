"""Etapa 2: FEM de barras contra o Karamba3D.

Entradas em testes/referencias/karamba/entrada_<caso>.json (geradas por
scripts/gerar_casos_karamba.py, versionadas); o Grasshopper roda o Karamba e o
exportador grava karamba_<caso>.json. Pulados até essa exportação existir.

As unidades de saída do Karamba são deduzidas (m, cm ou mm) e impressas; os
esforços são comparados pelo valor máximo de |N| e do momento resultante em
cada barra (independe da convenção de eixos locais, já que as seções são tubos).
"""
import json
import math

import pytest
import torch

from otimiza.modulos.estrutura.arquivo import de_dict
from otimiza.modulos.estrutura.fem import resolver
from testes.referencias import PASTA

PASTA_KR = PASTA / "karamba"
CASOS = sorted(p.name[len("karamba_"):-5] for p in PASTA_KR.glob("karamba_*.json")) if PASTA_KR.is_dir() else []
pytestmark = pytest.mark.skipif(not CASOS, reason="resultados do Karamba não exportados")


def _ler(nome):
    with open(nome, encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="module", params=CASOS or ["-"])
def caso(request):
    nome = request.param
    ent = _ler(PASTA_KR / f"entrada_{nome}.json")
    kr = _ler(PASTA_KR / f"karamba_{nome}.json")
    s = de_dict(ent)
    r = resolver([s], n_sub=1, flambagem=True, detalhes=True, device="cpu")
    return nome, s, r, kr


def _ramos(campo):
    """Lista de ramos (listas) de um campo exportado (árvore ou lista simples)."""
    if campo is None:
        return []
    if isinstance(campo, dict):
        return campo["ramos"]
    return [campo]


def _numeros(campo):
    """Todos os números (achatados), sem distinguir ramos."""
    def achatar(x):
        if isinstance(x, (int, float)):
            yield float(x)
        elif isinstance(x, list):
            for v in x:
                yield from achatar(v)
    return list(achatar(_ramos(campo)))


def _vetores(campo):
    vs = []
    for ramo in _ramos(campo):
        for v in ramo:
            if isinstance(v, list) and len(v) == 3:
                vs.append(v)
    return torch.tensor(vs, dtype=torch.float64)


def _melhor_escala(nosso, deles, escalas):
    """(erro relativo, nome da unidade) com a escala que melhor bate."""
    ref = nosso.abs().max()
    erros = {u: float((deles * k - nosso).abs().max() / ref) for u, k in escalas.items()}
    u = min(erros, key=erros.get)
    return erros[u], u


def _por_barra(campo, n_barras, reduz):
    """Máximo por barra de um esforço do Beam Forces (um ramo por barra, ou tudo junto)."""
    ramos = [r for r in _ramos(campo) if r]
    if len(ramos) == n_barras:
        return torch.tensor([reduz(r) for r in ramos], dtype=torch.float64)
    nums = _numeros(campo)
    if nums and len(nums) % n_barras == 0 and not isinstance(ramos[0][0], list):
        k = len(nums) // n_barras
        return torch.tensor([reduz(nums[i * k:(i + 1) * k]) for i in range(n_barras)], dtype=torch.float64)
    pytest.fail(f"estrutura inesperada: {len(ramos)} ramos para {n_barras} barras "
                f"(caminhos: {campo.get('caminhos', [])[:5] if isinstance(campo, dict) else '-'})")


def _abs_max(r):
    return max(math.sqrt(sum(c * c for c in v)) if isinstance(v, list) else abs(v) for v in r)


def test_massa(caso):
    nome, s, r, kr = caso
    m = _numeros(kr.get("massa"))
    if not m:
        pytest.skip("massa não exportada")
    print(f"\n{nome}: massa nós {float(r.massa[0]):.2f} kg, Karamba {m[0]:.2f}")
    assert abs(m[0] / float(r.massa[0]) - 1) < 0.01


def test_deslocamento_maximo(caso):
    nome, s, r, kr = caso
    d = _numeros(kr.get("desloc_max"))
    if not d:
        pytest.skip("Disp do Analyze não exportado")
    nosso = float(r.desloc_max[0]) * 100
    print(f"\n{nome}: deslocamento máx. nós {nosso:.4f} cm, Karamba {d[0]:.4f}")
    assert abs(d[0] / nosso - 1) < 0.01


def test_energia(caso):
    nome, s, r, kr = caso
    e = _numeros(kr.get("energia"))
    if not e:
        pytest.skip("energia não exportada")
    nosso = float(r.energia[0])
    print(f"\n{nome}: energia nós {nosso:.6f} kN·m, Karamba {e}")
    assert min(abs(v / nosso - 1) for v in e) < 0.01


def test_deslocamentos_nodais(caso):
    nome, s, r, kr = caso
    t = _vetores(kr.get("trans"))
    if t.numel() == 0:
        pytest.skip("Trans não exportado")
    assert t.shape[0] == s.n_nos, f"Karamba deu {t.shape[0]} nós, nós temos {s.n_nos}"
    u = r.deslocamentos[0]
    erro, un = _melhor_escala(u[:, :3], t, {"m": 1.0, "cm": 0.01, "mm": 0.001})
    print(f"\n{nome}: translações em {un}, erro máx. {erro:.2e} do maior deslocamento")
    assert erro < 0.01
    rot = _vetores(kr.get("rot"))
    if rot.numel() and not bool(s.trelica.all()):  # treliça: rotações nodais não têm sentido
        erro, un = _melhor_escala(u[:, 3:], rot, {"rad": 1.0, "mrad": 0.001})
        print(f"{nome}: rotações em {un}, erro máx. {erro:.2e}")
        assert erro < 0.01


def test_esforcos(caso):
    nome, s, r, kr = caso
    esf = r.esforcos[0]                                   # (Nb, 2, 6)
    for chave, nosso in (("N", esf[:, :, 0].abs().amax(1)),
                         ("M", esf[:, :, 4:6].norm(dim=2).amax(1))):
        if not _numeros(kr.get(chave)):
            continue
        deles = _por_barra(kr[chave], s.n_barras, _abs_max)
        ref = nosso.abs().max()
        if ref < 1e-9:  # ex.: momentos numa treliça
            assert deles.abs().max() < 1e-6
            continue
        erro = float((deles - nosso).abs().max() / ref)
        print(f"\n{nome}: {chave} máx. por barra, erro {erro:.2e} do maior ({float(ref):.3f})")
        assert erro < 0.01


def test_flambagem(caso):
    nome, s, r, kr = caso
    f = [v for v in _numeros(kr.get("flambagem")) if v > 0]
    if not f:
        pytest.skip("BLFacs não exportado")
    nosso = float(r.flambagem[0])
    print(f"\n{nome}: 1º fator de flambagem nós {nosso:.3f}, Karamba {min(f):.3f}")
    assert abs(min(f) / nosso - 1) < 0.03

"""Etapa 2: FEM de barras contra o Karamba3D.

Entradas em testes/referencias/karamba/entrada_<caso>.json (geradas por
scripts/gerar_casos_karamba.py, versionadas). No Grasshopper, o componente
"Validar Karamba" (rhino/grasshopper/karamba_validar.py, montado por
montar_karamba.py) roda cada caso pela API do Karamba e grava
karamba_<caso>.json. Pulados até essa exportação existir.

- Os nós são casados por posição (não depende da numeração do Karamba).
- A seção do Karamba informa as áreas de cisalhamento (Ay, Az): o Karamba usa
  vigas de Timoshenko, então comparamos com o nosso elemento usando as mesmas
  áreas (e mostramos também a diferença para Euler-Bernoulli).
- Unidades deduzidas (m/cm, m²/cm², kN/m²/kN/cm²) e impressas.
- Esforços comparados pelo máximo por barra de |N|, |Mt| e do momento fletor
  resultante √(My² + Mz²) (independe da convenção dos eixos locais; as seções
  são tubos).
"""
import dataclasses
import json
import math

import pytest
import torch

from otimiza.modulos.estrutura.arquivo import de_dict
from otimiza.modulos.estrutura.fem import PROPS, resolver
from testes.referencias import PASTA

PASTA_KR = PASTA / "karamba"
CASOS = sorted(p.name[len("karamba_"):-5] for p in PASTA_KR.glob("karamba_*.json")) if PASTA_KR.is_dir() else []
pytestmark = pytest.mark.skipif(not CASOS, reason="resultados do Karamba não exportados")
IA, IAY, IAZ = PROPS.index("A"), PROPS.index("Ay"), PROPS.index("Az")


def _ler(nome):
    with open(nome, encoding="utf-8") as f:
        return json.load(f)


def _escala(valor_deles, valor_nosso, escalas):
    """(fator, nome) que leva o valor deles à nossa unidade."""
    nome = min(escalas, key=lambda u: abs(math.log(abs(valor_deles * escalas[u] / valor_nosso))))
    return escalas[nome], nome


def _com_cisalhamento(s, kr):
    """Cópia da estrutura com as áreas de cisalhamento do Karamba (convertidas para m²)."""
    sk = kr.get("secao_karamba") or {}
    if not isinstance(sk.get("Ay"), float) or not isinstance(sk.get("A"), float) or sk["Ay"] <= 0:
        return None, "sem Ay/Az do Karamba"
    k, un = _escala(sk["A"], float(s.props[0, IA]), {"m²": 1.0, "cm²": 1e-4, "mm²": 1e-6})
    props = s.props.clone()
    props[:, IAY] = sk["Ay"] * k
    props[:, IAZ] = sk.get("Az", sk["Ay"]) * k
    return dataclasses.replace(s, props=props), f"Ay = {sk['Ay'] * k / float(s.props[0, IA]):.3f}·A"


@pytest.fixture(scope="module", params=CASOS or ["-"])
def caso(request):
    nome = request.param
    s = de_dict(_ler(PASTA_KR / f"entrada_{nome}.json"))
    kr = _ler(PASTA_KR / f"karamba_{nome}.json")
    euler = resolver([s], n_sub=1, flambagem=True, detalhes=True, device="cpu")
    st, info = _com_cisalhamento(s, kr)
    timo = resolver([st], n_sub=1, flambagem=True, detalhes=True, device="cpu") if st is not None else euler
    print(f"\n{nome}: Karamba {kr.get('versao_karamba')}, licença {kr.get('licenca')}, "
          f"seção em {kr.get('unidade_secao')}, cisalhamento: {info}")
    return nome, s, timo, euler, kr


def _vetores(x):
    """Todas as triplas [x, y, z] dentro de listas aninhadas, na ordem."""
    if isinstance(x, list) and len(x) == 3 and all(isinstance(v, (int, float)) for v in x):
        return [x]
    if isinstance(x, list):
        return [v for item in x for v in _vetores(item)]
    return []


def _numeros(x):
    if isinstance(x, (int, float)):
        return [float(x)]
    if isinstance(x, list):
        return [v for item in x for v in _numeros(item)]
    return []


def _ordem_nos(s, kr):
    """Para cada nó do Karamba, o índice do nosso nó na mesma posição."""
    pos = torch.tensor(kr.get("posicoes_nos") or kr["nos_karamba"], dtype=torch.float64)
    d = torch.cdist(pos, s.nos)
    dist, idx = d.min(dim=1)
    assert float(dist.max()) < 1e-3, f"nós do Karamba fora das nossas posições (máx. {float(dist.max()):.2e} m)"
    assert len(set(idx.tolist())) == s.n_nos
    return idx


def test_secao_e_material(caso):
    nome, s, _, _, kr = caso
    sk, mk = kr.get("secao_karamba", {}), kr.get("material_karamba", {})
    for chave, nosso, escalas in (
            ("A", float(s.props[0, IA]), {"m²": 1.0, "cm²": 1e-4}),
            ("Iyy", float(s.props[0, PROPS.index("Iy")]), {"m^4": 1.0, "cm^4": 1e-8})):
        if isinstance(sk.get(chave), float):
            k, un = _escala(sk[chave], nosso, escalas)
            print(f"{nome}: {chave} Karamba {sk[chave]} {un} -> erro {sk[chave] * k / nosso - 1:+.2e}")
            assert abs(sk[chave] * k / nosso - 1) < 0.005
    if isinstance(mk.get("E"), float):
        nosso = float(s.props[0, PROPS.index("E")])
        k, un = _escala(mk["E"], nosso, {"kN/m²": 1.0, "kN/cm²": 1e4, "N/m²": 1e-3, "MPa": 1e3})
        print(f"{nome}: E Karamba {mk['E']} {un}; G12 {mk.get('G12')}, G3 {mk.get('G3')}, gamma {mk.get('gamma')}")
        assert abs(mk["E"] * k / nosso - 1) < 0.005


def test_massa(caso):
    nome, s, r, _, kr = caso
    m = kr.get("massa")
    if not isinstance(m, float):
        pytest.skip("massa não exportada")
    razao = m / float(r.massa[0])
    # o Karamba guarda o peso específico γ = 78,5 kN/m³ e a massa = γ/g; nós usamos ρ = 7850 kg/m³
    print(f"{nome}: massa nós {float(r.massa[0]):.2f} kg, Karamba {m:.2f} (razão {razao:.4f})")
    assert 0.97 < razao < 1.03


def test_deslocamento_maximo(caso):
    nome, s, r, re, kr = caso
    d = _numeros(kr.get("desloc_max"))
    if not d:
        pytest.skip("desloc. máx. não exportado")
    nosso = float(r.desloc_max[0])
    k, un = _escala(d[0], nosso, {"m": 1.0, "cm": 0.01, "mm": 0.001})
    print(f"{nome}: desloc. máx. Karamba {d[0]} {un}; nós {nosso:.6f} m "
          f"(Euler-Bernoulli {float(re.desloc_max[0]):.6f}); erro {d[0] * k / nosso - 1:+.2e}")
    assert abs(d[0] * k / nosso - 1) < 0.01


def test_energia(caso):
    nome, s, r, _, kr = caso
    e = _numeros(kr.get("energia"))
    if not e:
        pytest.skip("energia não exportada")
    nosso = float(r.energia[0])
    k, un = _escala(e[0], nosso, {"kN·m": 1.0, "kN·cm": 0.01, "N·m": 1e-3})
    print(f"{nome}: energia Karamba {e[0]} {un}; nós {nosso:.6f} kN·m; erro {e[0] * k / nosso - 1:+.2e}")
    assert abs(e[0] * k / nosso - 1) < 0.01


def test_deslocamentos_nodais(caso):
    nome, s, r, _, kr = caso
    t = torch.tensor(_vetores(kr.get("trans")), dtype=torch.float64)
    if t.numel() == 0:
        pytest.skip("deslocamentos nodais não exportados")
    assert t.shape[0] == s.n_nos, f"Karamba deu {t.shape[0]} vetores, temos {s.n_nos} nós"
    idx = _ordem_nos(s, kr)
    u = r.deslocamentos[0][idx]
    ref = u[:, :3].abs().max()
    k, un = _escala(float(t.abs().max()), float(ref), {"m": 1.0, "cm": 0.01, "mm": 0.001})
    erro = float((t * k - u[:, :3]).abs().max() / ref)
    print(f"{nome}: translações em {un}, erro máx. {erro:.2e} do maior deslocamento")
    assert erro < 0.01
    rot = torch.tensor(_vetores(kr.get("rot")), dtype=torch.float64)
    if rot.shape[0] == s.n_nos and not bool(s.trelica.all()):   # treliça: rotação nodal sem sentido
        ref = u[:, 3:].abs().max()
        erro = float((rot - u[:, 3:]).abs().max() / ref)
        print(f"{nome}: rotações (rad), erro máx. {erro:.2e} da maior")
        assert erro < 0.01


def _por_barra(kr, chave, nb):
    """[barra][posição][3] a partir de forcas/momentos ([caso?][elemento][posição][3])."""
    x = kr.get(chave)
    if not x:
        return None
    while isinstance(x, list) and len(x) == 1 and nb != 1:   # tira o nível do caso de carga
        x = x[0]
    assert len(x) == nb, f"{chave}: {len(x)} elementos, esperados {nb}"
    ids = kr.get("ids_elem")
    ordem = [int(i[1:]) for i in ids] if ids and all(i and i.startswith("b") for i in ids) else list(range(nb))
    saida = [None] * nb
    for e, b in enumerate(ordem):
        saida[b] = _vetores(x[e])
    return torch.tensor(saida, dtype=torch.float64)          # (nb, P, 3)


def test_esforcos(caso):
    nome, s, r, _, kr = caso
    f, m = _por_barra(kr, "forcas", s.n_barras), _por_barra(kr, "momentos", s.n_barras)
    if f is None:
        pytest.skip("esforços nas barras não exportados")
    esf = r.esforcos[0]                                              # (Nb, 2, 6)
    pares = [("N", f[..., 0].abs().amax(1), esf[:, :, 0].abs().amax(1))]
    if m is not None:
        pares += [("Mt", m[..., 0].abs().amax(1), esf[:, :, 3].abs().amax(1)),
                  ("M", m[..., 1:].norm(dim=2).amax(1), esf[:, :, 4:6].norm(dim=2).amax(1))]
    for chave, deles, nosso in pares:
        ref = float(nosso.abs().max())
        if ref < 1e-9:                                               # ex.: momentos numa treliça
            assert float(deles.abs().max()) < 1e-6
            continue
        erro = float((deles - nosso).abs().max()) / ref
        print(f"{nome}: {chave} máx. por barra, erro {erro:.2e} do maior ({ref:.3f})")
        assert erro < 0.01


def test_flambagem(caso):
    nome, s, r, re, kr = caso
    f = [v for v in _numeros(kr.get("flambagem")) if v > 0]
    if not f:
        pytest.skip("fatores de flambagem não exportados")
    nosso = float(r.flambagem[0])
    print(f"{nome}: 1º fator de flambagem Karamba {min(f):.3f}; nós {nosso:.3f} "
          f"(Euler-Bernoulli {float(re.flambagem[0]):.3f})")
    assert abs(min(f) / nosso - 1) < 0.03

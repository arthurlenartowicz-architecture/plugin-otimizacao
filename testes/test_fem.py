"""FEM de barras contra resultados de livro-texto (viga, console, treliça, Euler)."""
import math

import pytest
import torch

from otimiza.modulos.estrutura.fem import (ACO_S235, ARTICULADO, ENGASTE, Estrutura, resolver,
                                           retangulo, tubo_circular)

SEC = tubo_circular(0.2, 0.01)
E, G, FY = ACO_S235.E, ACO_S235.G, ACO_S235.fy


def rel(a, b):
    return abs(float(a) - float(b)) / abs(float(b))


def console(L=3.0, cargas=None, **kw):
    return Estrutura.criar([[0, 0, 0], [L, 0, 0]], [[0, 1]], SEC, apoios={0: ENGASTE},
                           cargas=cargas, **kw)


def test_console_carga_na_ponta():
    L, P = 3.0, 5.0
    r = resolver([console(L, {1: (0, 0, -P)})], detalhes=True)
    u = r.deslocamentos[0][1]
    assert rel(-u[2], P * L ** 3 / (3 * E * SEC.Iy)) < 1e-9
    assert rel(u[4], P * L ** 2 / (2 * E * SEC.Iy)) < 1e-9   # θy > 0 com carga em −z
    assert rel(r.energia[0], P * P * L ** 3 / (6 * E * SEC.Iy)) < 1e-9
    m_base = r.esforcos[0][0, 0]
    assert rel(abs(m_base[4]), P * L) < 1e-9 and rel(abs(m_base[2]), P) < 1e-9
    assert rel(r.utilizacao[0], P * L / SEC.Wy / FY) < 1e-9
    assert rel(r.massa[0], SEC.A * L * ACO_S235.rho) < 1e-12


def test_console_axial_torcao_e_lateral():
    L, N, Tq, Py = 2.0, 40.0, 3.0, 2.0
    r = resolver([console(L, {1: (N, Py, 0, Tq, 0, 0)})], detalhes=True)
    u = r.deslocamentos[0][1]
    assert rel(u[0], N * L / (E * SEC.A)) < 1e-9
    assert rel(u[3], Tq * L / (G * SEC.J)) < 1e-9
    assert rel(u[1], Py * L ** 3 / (3 * E * SEC.Iz)) < 1e-9
    s = r.esforcos[0][0, 0]
    assert rel(s[0], N) < 1e-9 and rel(abs(s[3]), Tq) < 1e-9  # tração positiva


def test_console_peso_proprio():
    L = 4.0
    r = resolver([console(L, gravidade=9.81)], detalhes=True)
    q = ACO_S235.rho * SEC.A * 9.81 / 1000
    assert rel(-r.deslocamentos[0][1][2], q * L ** 4 / (8 * E * SEC.Iy)) < 1e-9
    assert rel(abs(r.esforcos[0][0, 0, 4]), q * L ** 2 / 2) < 1e-9


@pytest.mark.parametrize("n_sub", [1, 2, 4])
def test_viga_biapoiada_carga_distribuida(n_sub):
    L, w = 6.0, 10.0
    sec = retangulo(0.2, 0.5)
    s = Estrutura.criar([[0, 0, 0], [L / 2, 0, 0], [L, 0, 0]], [[0, 1], [1, 2]], sec,
                        apoios={0: (1, 1, 1, 1, 0, 0), 2: (0, 1, 1, 0, 0, 0)},
                        carga_dist={0: (0, 0, -w), 1: (0, 0, -w)})
    r = resolver([s], n_sub=n_sub, detalhes=True)
    f = 5 * w * L ** 4 / (384 * E * sec.Iy)
    assert rel(-r.deslocamentos[0][1][2], f) < 1e-9          # nó no meio do vão: exato
    assert rel(r.desloc_max[0], f) < 1e-9
    esf = r.esforcos[0]
    assert rel(abs(esf[0, 1, 4]), w * L ** 2 / 8) < 1e-9      # momento máximo no meio
    assert rel(abs(esf[0, 0, 2]), w * L / 2) < 1e-9           # cortante no apoio
    assert abs(esf[0, 0, 4]) < 1e-9                           # momento nulo no apoio
    assert rel(r.utilizacao[0], w * L ** 2 / 8 / sec.Wy / FY) < 1e-9


def test_viga_carga_distribuida_inclinada_um_elemento():
    """Viga biapoiada num elemento só, inclinada no plano xz: flecha no meio via n_sub=2."""
    L, w = 5.0, 4.0
    c, s_ = math.cos(0.3), math.sin(0.3)
    s = Estrutura.criar([[0, 0, 0], [L * c, 0, L * s_]], [[0, 1]], SEC,
                        apoios={0: (1, 1, 1, 1, 0, 0), 1: (1, 1, 1, 0, 0, 0)},
                        carga_dist={0: (0, -w, 0)})  # perpendicular ao plano da viga
    r = resolver([s], n_sub=2)
    assert rel(r.desloc_max[0], 5 * w * L ** 4 / (384 * E * SEC.Iz)) < 1e-9


def test_trelica_tripe():
    """Três barras até um ápice, carga vertical: N = −P/(3 sen α), δ = P L/(3 EA sen² α)."""
    P, h, raio = 30.0, 2.0, 1.5
    base = [[raio * math.cos(a), raio * math.sin(a), 0] for a in (0, 2 * math.pi / 3, 4 * math.pi / 3)]
    s = Estrutura.criar(base + [[0, 0, h]], [[0, 3], [1, 3], [2, 3]], SEC, trelica=True,
                        apoios={0: ARTICULADO, 1: ARTICULADO, 2: ARTICULADO}, cargas={3: (0, 0, -P)})
    r = resolver([s], n_sub=4, detalhes=True)
    L = math.hypot(raio, h)
    sa = h / L
    assert rel(-r.deslocamentos[0][3][2], P * L / (3 * E * SEC.A * sa ** 2)) < 1e-9
    n = r.esforcos[0][:, :, 0]
    assert torch.allclose(n, torch.full_like(n, -P / (3 * sa)), rtol=1e-9)
    assert rel(r.utilizacao[0], P / (3 * sa) / SEC.A / FY) < 1e-9


def test_trelica_plana_triangulo():
    """Triângulo isostático (apoio fixo + móvel), carga no topo: esforços pelo método dos nós."""
    P, vao, h = 10.0, 4.0, 3.0
    s = Estrutura.criar([[0, 0, 0], [vao, 0, 0], [vao / 2, 0, h]], [[0, 1], [0, 2], [1, 2]], SEC,
                        trelica=True, apoios={0: ARTICULADO, 1: (0, 1, 1, 0, 0, 0), 2: (0, 1, 0, 0, 0, 0)},
                        cargas={2: (0, 0, -P)})
    r = resolver([s], detalhes=True)
    d = math.hypot(vao / 2, h)
    n_diag = -P / 2 * d / h
    n_banzo = P / 2 * (vao / 2) / h
    n = r.esforcos[0][:, 0, 0]
    assert rel(n[1], n_diag) < 1e-9 and rel(n[2], n_diag) < 1e-9 and rel(n[0], n_banzo) < 1e-9


@pytest.mark.parametrize("caso", ["console", "biarticulado"])
def test_flambagem_euler(caso):
    L, P = 4.0, 1.0
    I = SEC.Iy
    if caso == "console":
        ap = {0: ENGASTE}
        pcr = math.pi ** 2 * E * I / (4 * L ** 2)
    else:
        ap = {0: ARTICULADO, 1: (1, 1, 0, 0, 0, 1)}
        pcr = math.pi ** 2 * E * I / L ** 2
    s = Estrutura.criar([[0, 0, 0], [0, 0, L]], [[0, 1]], SEC, apoios=ap, cargas={1: (0, 0, -P)})
    r = resolver([s], n_sub=8, flambagem=True)
    assert rel(r.flambagem[0], pcr / P) < 1e-4


def test_mecanismo_vira_nan():
    s = Estrutura.criar([[0, 0, 0], [1, 0, 0]], [[0, 1]], SEC, trelica=True,
                        apoios={0: ARTICULADO}, cargas={1: (0, 0, -1)})
    r = resolver([s, console(cargas={1: (0, 0, -1)})])
    assert not r.estavel[0] and r.estavel[1]
    assert torch.isnan(r.desloc_max[0]) and torch.isfinite(r.desloc_max[1])


def test_lote_igual_a_individual():
    """Variantes de tamanhos diferentes no mesmo lote dão o mesmo que uma a uma."""
    def portico(h, vao, n_pav):
        nos, barras, ap = [], [], {}
        for k in range(n_pav + 1):
            for x, y in ((0, 0), (vao, 0), (vao, vao), (0, vao)):
                nos.append([x, y, k * h])
        for k in range(n_pav):
            for c in range(4):
                barras.append([4 * k + c, 4 * (k + 1) + c])          # pilares
                barras.append([4 * (k + 1) + c, 4 * (k + 1) + (c + 1) % 4])  # vigas
        for c in range(4):
            ap[c] = ENGASTE
        topo = 4 * n_pav
        return Estrutura.criar(nos, barras, SEC, apoios=ap, cargas={topo: (5, 2, -10)},
                               carga_dist={len(barras) - 1: (0, 0, -3)}, gravidade=9.81)

    vs = [portico(3, 5, 1), portico(3.5, 6, 3), portico(2.8, 4, 2)]
    junto = resolver(vs, n_sub=2, flambagem=True, detalhes=True)
    for i, v in enumerate(vs):
        so = resolver([v], n_sub=2, flambagem=True, detalhes=True)
        for nome in ("desloc_max", "energia", "massa", "utilizacao", "flambagem"):
            assert rel(getattr(junto, nome)[i], getattr(so, nome)[0]) < 1e-9, nome
        assert torch.allclose(junto.deslocamentos[i], so.deslocamentos[0], atol=1e-14)
    # lotes pequenos de memória (várias partes) também
    partes = resolver(vs, n_sub=2, memoria_max=1.0)
    assert torch.allclose(partes.energia, junto.energia, rtol=1e-12)


def test_equilibrio_reacoes():
    """Soma das forças nos apoios = carga total (pórtico com peso próprio)."""
    nos = [[0, 0, 0], [0, 0, 3], [4, 0, 3], [4, 0, 0]]
    s = Estrutura.criar(nos, [[0, 1], [1, 2], [2, 3]], SEC, apoios={0: ENGASTE, 3: ENGASTE},
                        cargas={1: (8, 0, 0)}, carga_dist={1: (0, 0, -5)}, gravidade=9.81)
    r = resolver([s], detalhes=True)
    esf = r.esforcos[0]
    # pilares verticais: x local = +Z global; força no apoio = −(esforço na seção inicial)
    total_z = 5 * 4 + s.massa() * 9.81 / 1000
    reac_z = -(esf[0, 0, 0] + esf[2, 1, 0])  # barra 2 vai de cima para baixo  # N nos pilares (compressão negativa)
    assert rel(reac_z, total_z) < 1e-9

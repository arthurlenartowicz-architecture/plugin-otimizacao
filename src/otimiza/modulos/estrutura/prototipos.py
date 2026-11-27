"""Estruturas paramétricas para testes e benchmark."""
from __future__ import annotations

from otimiza.modulos.estrutura.fem import ARTICULADO, ENGASTE, Estrutura, Secao, tubo_circular


def trelica_pratt(vao: float = 12.0, altura: float = 1.5, n_paineis: int = 6, carga: float = 10.0,
                  secao: Secao | None = None, gravidade: float = 9.81) -> Estrutura:
    """Treliça plana (plano xz) biapoiada, carga `carga` kN em cada nó do banzo inferior.
    Todos os nós travados em y (fora do plano)."""
    n = n_paineis
    dx = vao / n
    nos = [[i * dx, 0, 0] for i in range(n + 1)] + [[i * dx, 0, altura] for i in range(n + 1)]
    sup = lambda i: n + 1 + i  # noqa: E731
    barras = [[i, i + 1] for i in range(n)] + [[sup(i), sup(i + 1)] for i in range(n)]
    barras += [[i, sup(i)] for i in range(n + 1)]                       # montantes
    barras += [[i + 1, sup(i)] if i < n // 2 else [i, sup(i + 1)] for i in range(n)]  # diagonais
    apoios = {k: (False, True, False, False, False, False) for k in range(2 * n + 2)}
    apoios[0] = ARTICULADO
    apoios[n] = (False, True, True, False, False, False)
    cargas = {i: (0, 0, -carga) for i in range(1, n)}
    return Estrutura.criar(nos, barras, secao or tubo_circular(0.1143, 0.004), trelica=True,
                           apoios=apoios, cargas=cargas, gravidade=gravidade)


def portico_espacial(vao_x: float = 6.0, vao_y: float = 5.0, pe_direito: float = 3.0,
                     n_pav: int = 3, carga_viga: float = 15.0, vento: float = 5.0,
                     pilar: Secao | None = None, viga: Secao | None = None,
                     gravidade: float = 9.81) -> Estrutura:
    """Pórtico de 4 pilares engastados, vigas no contorno de cada pavimento com carga
    distribuída `carga_viga` kN/m e força horizontal `vento` kN (+x) em cada pavimento."""
    pilar = pilar or tubo_circular(0.2, 0.01)
    viga = viga or tubo_circular(0.2, 0.008)
    cantos = [(0, 0), (vao_x, 0), (vao_x, vao_y), (0, vao_y)]
    nos = [[x, y, k * pe_direito] for k in range(n_pav + 1) for x, y in cantos]
    barras, secoes, dist, cargas = [], [], {}, {}
    for k in range(n_pav):
        for c in range(4):
            barras.append([4 * k + c, 4 * (k + 1) + c])
            secoes.append(pilar)
        for c in range(4):
            dist[len(barras)] = (0, 0, -carga_viga)
            barras.append([4 * (k + 1) + c, 4 * (k + 1) + (c + 1) % 4])
            secoes.append(viga)
        cargas[4 * (k + 1)] = (vento, 0, 0)
    return Estrutura.criar(nos, barras, secoes, apoios={c: ENGASTE for c in range(4)},
                           cargas=cargas, carga_dist=dist, gravidade=gravidade)


def trelica_espacial(nx: int = 4, ny: int = 3, modulo: float = 2.0, altura: float = 1.5,
                     carga: float = 8.0, secao: Secao | None = None,
                     gravidade: float = 0.0) -> Estrutura:
    """Malha espacial de duas camadas (square-on-square offset): banzo inferior nx x ny
    modulos, nos superiores no centro de cada modulo, 4 diagonais por no superior.
    Apoios articulados nos 4 cantos inferiores; carga vertical em cada no superior."""
    def inf(i, j):
        return j * (nx + 1) + i

    n_inf = (nx + 1) * (ny + 1)

    def sup(i, j):
        return n_inf + j * nx + i

    nos = [[i * modulo, j * modulo, 0] for j in range(ny + 1) for i in range(nx + 1)]
    nos += [[(i + 0.5) * modulo, (j + 0.5) * modulo, altura] for j in range(ny) for i in range(nx)]
    barras = [[inf(i, j), inf(i + 1, j)] for j in range(ny + 1) for i in range(nx)]
    barras += [[inf(i, j), inf(i, j + 1)] for j in range(ny) for i in range(nx + 1)]
    barras += [[sup(i, j), sup(i + 1, j)] for j in range(ny) for i in range(nx - 1)]
    barras += [[sup(i, j), sup(i, j + 1)] for j in range(ny - 1) for i in range(nx)]
    for j in range(ny):
        for i in range(nx):
            for a, b in ((i, j), (i + 1, j), (i, j + 1), (i + 1, j + 1)):
                barras.append([sup(i, j), inf(a, b)])
    cantos = [inf(0, 0), inf(nx, 0), inf(0, ny), inf(nx, ny)]
    cargas = {sup(i, j): (0, 0, -carga) for j in range(ny) for i in range(nx)}
    return Estrutura.criar(nos, barras, secao or tubo_circular(0.1143, 0.004), trelica=True,
                           apoios={c: ARTICULADO for c in cantos}, cargas=cargas,
                           gravidade=gravidade)

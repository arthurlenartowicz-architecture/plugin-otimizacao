#! python 2
# -*- coding: utf-8 -*-
"""Sala de exemplo em Breps (o mesmo projeto de `nucleo/prototipos.predio`).

Serve para validar o caminho GH → malha → protocolo: avaliada pelo componente
Otimizar, tem de dar as métricas do protótipo em Python com os mesmos parâmetros
(testes/test_gh.py). Sala 6 × 12 × 3 m, uma janela centrada por fachada (razão
janela/parede, margem 0,2 m, peitoril 0,8 m), girada em torno da origem; torre
vizinha ao norte como contexto. Unidades do modelo: metros (ou qualquer uma: a
escala vai para o servidor).

ENTRADAS  janela_N, janela_L, janela_S, janela_O (0 a 0,8), rotacao_graus
SAÍDAS    fachada, vidro, cobertura, piso, contexto (Breps)
"""
import math

import Rhino
import Rhino.Geometry as rg

LARGURA, PROFUNDIDADE, ALTURA = 6.0, 12.0, 3.0
PEITORIL, MARGEM = 0.8, 0.2
TORRE = ((-10.0, 14.0, 0.0), (16.0, 24.0, 30.0))
ESCALA = 1.0 / Rhino.RhinoMath.UnitScale(Rhino.RhinoDoc.ActiveDoc.ModelUnitSystem,
                                           Rhino.UnitSystem.Meters)   # metro → modelo
TOL = Rhino.RhinoDoc.ActiveDoc.ModelAbsoluteTolerance


def _v(x):
    return 0.0 if x is None else float(x)


def _ret(p0, u, v):
    pts = [p0, p0 + u, p0 + u + v, p0 + v, p0]
    return rg.PolylineCurve([rg.Point3d(p * ESCALA) for p in pts])


def _plana(curvas, normal):
    """Brep plano (com furos) com a normal para o lado pedido."""
    b = rg.Brep.CreatePlanarBreps(curvas, TOL)[0]
    m = rg.Mesh.CreateFromBrep(b, rg.MeshingParameters.Minimal)[0]
    m.FaceNormals.ComputeFaceNormals()
    if rg.Vector3d.Multiply(rg.Vector3d(m.FaceNormals[0]), normal) < 0:
        b.Flip()
    return b


def parede(p0, u, razao, fachadas, vidros):
    p0, u = rg.Point3d(*p0), rg.Vector3d(*u)
    L = u.Length
    e = rg.Vector3d(u)
    e.Unitize()
    z = rg.Vector3d(0, 0, ALTURA)
    normal = rg.Vector3d.CrossProduct(u, rg.Vector3d.ZAxis)
    externa = _ret(p0, u, z)
    if razao <= 0:
        fachadas.append(_plana([externa], normal))
        return
    lj = max(L - 2 * MARGEM, 0.1)
    hj = min(razao * L * ALTURA / lj, ALTURA - 0.1)
    pz = max(min(PEITORIL, ALTURA - hj - 0.05), 0.05)
    x0 = (L - lj) / 2
    janela = _ret(p0 + e * x0 + rg.Vector3d(0, 0, pz), e * lj, rg.Vector3d(0, 0, hj))
    fachadas.append(_plana([externa, janela], normal))
    vidros.append(_plana([janela.DuplicateCurve()], normal))


def montar(janelas, rotacao):
    W, D = LARGURA, PROFUNDIDADE
    fachadas, vidros = [], []
    parede((0, 0, 0), (W, 0, 0), janelas["S"], fachadas, vidros)
    parede((W, 0, 0), (0, D, 0), janelas["L"], fachadas, vidros)
    parede((W, D, 0), (-W, 0, 0), janelas["N"], fachadas, vidros)
    parede((0, D, 0), (0, -D, 0), janelas["O"], fachadas, vidros)
    piso = _plana([_ret(rg.Point3d(0, 0, 0), rg.Vector3d(0, D, 0), rg.Vector3d(W, 0, 0))],
                  -rg.Vector3d.ZAxis)
    cob = _plana([_ret(rg.Point3d(0, 0, ALTURA), rg.Vector3d(W, 0, 0), rg.Vector3d(0, D, 0))],
                 rg.Vector3d.ZAxis)
    giro = rg.Transform.Rotation(math.radians(rotacao), rg.Vector3d.ZAxis, rg.Point3d.Origin)
    for b in fachadas + vidros + [piso, cob]:
        b.Transform(giro)
    a, b = TORRE
    caixa = rg.Box(rg.BoundingBox(rg.Point3d(*a) * ESCALA, rg.Point3d(*b) * ESCALA)).ToBrep()
    return fachadas, vidros, [cob], [piso], [caixa]


fachada, vidro, cobertura, piso, contexto = montar(
    {"N": _v(janela_N), "L": _v(janela_L), "S": _v(janela_S), "O": _v(janela_O)},  # noqa: F821
    _v(rotacao_graus))  # noqa: F821

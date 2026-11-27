# -*- coding: utf-8 -*-
"""Lê testes/referencias/karamba/entrada_<nome>.json e entrega a geometria e as
cargas para os componentes do Karamba (montado por montar_definicao.py).

ENTRADA:  arquivo (str) caminho do entrada_<nome>.json
SAÍDAS:   nome, nos (Point3d, na ordem do nosso modelo), vigas (Line), trelicas (Line),
          apoios (Point3d do 1º grupo), condicoes (6 bools), pontos_carga (Point3d),
          forcas (Vector3d, kN), momentos (Vector3d, kN·m), info
"""
import io
import json

import Rhino
import Rhino.Geometry as rg


def _ler():
    with io.open(arquivo, encoding="utf-8") as f:
        return json.load(f)


def _executar():
    d = _ler()
    k = Rhino.RhinoMath.UnitScale(Rhino.UnitSystem.Meters, Rhino.RhinoDoc.ActiveDoc.ModelUnitSystem)
    pts = [rg.Point3d(x * k, y * k, z * k) for x, y, z in d["nos"]]
    vig, tre = [], []
    for (i, j), t in zip(d["barras"], d["trelica"]):
        (tre if t else vig).append(rg.Line(pts[i], pts[j]))
    ap = d["apoios"][0]
    cargas = d["cargas"]
    msgs = ["%s: %d nós, %d vigas, %d treliças, %d cargas" % (d["nome"], len(pts), len(vig), len(tre),
                                                             len(cargas))]
    if len(d["apoios"]) > 1:
        msgs.append("AVISO: só o 1º grupo de apoios foi usado")
    return (d["nome"], pts, vig, tre, [pts[n] for n in ap["nos"]], [bool(c) for c in ap["condicoes"]],
            [pts[c["no"]] for c in cargas], [rg.Vector3d(*c["forca"]) for c in cargas],
            [rg.Vector3d(*c["momento"]) for c in cargas], "\n".join(msgs))


if arquivo:
    nome, nos, vigas, trelicas, apoios, condicoes, pontos_carga, forcas, momentos, info = _executar()
else:
    info = "Ligue o caminho do entrada_<nome>.json em 'arquivo'."

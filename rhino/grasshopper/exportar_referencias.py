# -*- coding: utf-8 -*-
"""Exporta casos de referência do Grasshopper/Ladybug para testes/referencias/.

Cole este código num componente Python do Grasshopper (Rhino 8: "Script" em
Python 3, ou o GhPython antigo; funciona nos dois). Crie estas ENTRADAS
(clique com o botão direito no componente para adicionar e renomear):

  pasta          str        caminho de testes/referencias do projeto
  caso           str        nome do caso, ex.: "caixa_sp"
  geometria      Brep/Mesh  (lista) geometria do estudo
  contexto       Brep/Mesh  (lista, opcional) entorno que faz sombra
  pontos         Point3d    (lista, opcional) sensores, ex.: saída do LB Generate Point Grid
  normais        Vector3d   (lista, opcional) normais dos sensores
  epw            str        (opcional) caminho do .epw usado no estudo
  resultados     float      (lista, opcional) resultado do Ladybug por sensor
  nome_resultado str        (opcional) ex.: "radiacao_kwh_m2" ou "horas_sol"
  exportar       bool       ligue a um Button

SAÍDA:  log

Arquivos gravados em <pasta>/<caso>/ (tudo em metros):
  geometria.json, contexto.json  malha triangulada (vertices, faces)
  sensores.json                  pontos e normais
  sol_ladybug.json               posição do sol do Ladybug nas 8760 horas (meio da hora)
  ceu_ladybug.json               vetores dos patches Tregenza/Reinhart do Ladybug
  resultado_<nome>.json          valores do Ladybug por sensor
"""
import json
import os

import Rhino
import Rhino.Geometry as rg
import System


def _coagir(o, tipo):
    """No GhPython (dica de tipo padrão) a geometria chega como Guid; converte."""
    if isinstance(o, System.Guid):
        import rhinoscriptsyntax as rs
        if tipo == "ponto":
            return rs.coerce3dpoint(o)
        if tipo == "vetor":
            return rs.coerce3dvector(o)
        return rs.coercegeometry(o)
    return o


def _escala_para_metros():
    doc = Rhino.RhinoDoc.ActiveDoc
    return Rhino.RhinoMath.UnitScale(doc.ModelUnitSystem, Rhino.UnitSystem.Meters)


def _para_malha(objs):
    """Junta Breps/Meshes numa malha só, triangulada."""
    malha = rg.Mesh()
    params = rg.MeshingParameters.Default
    for o in objs or []:
        o = _coagir(o, "geometria")
        if o is None:
            continue
        if isinstance(o, rg.Mesh):
            malha.Append(o)
        elif isinstance(o, rg.Brep):
            for m in rg.Mesh.CreateFromBrep(o, params) or []:
                malha.Append(m)
        elif isinstance(o, rg.Extrusion):
            for m in rg.Mesh.CreateFromBrep(o.ToBrep(), params) or []:
                malha.Append(m)
    malha.Faces.ConvertQuadsToTriangles()
    malha.Compact()
    return malha


def _malha_json(malha, k):
    vs = [[v.X * k, v.Y * k, v.Z * k] for v in malha.Vertices]
    fs = [[f.A, f.B, f.C] for f in malha.Faces]
    return {"vertices": vs, "faces": fs}


def _gravar(caminho, dados):
    with open(caminho, "w") as f:
        json.dump(dados, f)
    return os.path.basename(caminho)


def _sol_ladybug(caminho_epw):
    from ladybug.epw import EPW
    from ladybug.sunpath import Sunpath

    clima = EPW(caminho_epw)
    loc = clima.location
    sp = Sunpath.from_location(loc)
    alt, az = [], []
    for i in range(8760):
        s = sp.calculate_sun_from_hoy(i + 0.5)  # meio da hora, igual ao nosso motor
        alt.append(s.altitude)
        az.append(s.azimuth)
    return {
        "epw": os.path.basename(caminho_epw),
        "latitude": loc.latitude, "longitude": loc.longitude,
        "fuso": loc.time_zone, "elevacao": loc.elevation,
        "hoy": [i + 0.5 for i in range(8760)],
        "altitude": alt, "azimute": az,
    }


def _ceu_ladybug():
    from ladybug.viewsphere import view_sphere

    def vets(lista):
        return [[v.x, v.y, v.z] for v in lista]

    return {
        "tregenza": vets(view_sphere.tregenza_dome_vectors),
        "reinhart": vets(view_sphere.reinhart_dome_vectors),
    }


def executar():
    msgs = []
    if not exportar:
        return "Aperte o botão 'exportar' para gravar."
    if not pasta or not caso:
        return "Preencha 'pasta' e 'caso'."
    destino = os.path.join(pasta, caso)
    if not os.path.isdir(destino):
        os.makedirs(destino)
    k = _escala_para_metros()

    if geometria:
        m = _para_malha(geometria)
        msgs.append(_gravar(os.path.join(destino, "geometria.json"), _malha_json(m, k)))
    if contexto:
        m = _para_malha(contexto)
        msgs.append(_gravar(os.path.join(destino, "contexto.json"), _malha_json(m, k)))

    if pontos:
        pts = [_coagir(p, "ponto") for p in pontos]
        sens = {"pontos": [[p.X * k, p.Y * k, p.Z * k] for p in pts]}
        if normais:
            if len(normais) != len(pontos):
                return "ERRO: 'normais' e 'pontos' têm tamanhos diferentes."
            ns = []
            for n in normais:
                n = rg.Vector3d(_coagir(n, "vetor"))
                n.Unitize()
                ns.append([n.X, n.Y, n.Z])
            sens["normais"] = ns
        msgs.append(_gravar(os.path.join(destino, "sensores.json"), sens))

    if epw:
        try:
            msgs.append(_gravar(os.path.join(destino, "sol_ladybug.json"), _sol_ladybug(epw)))
        except Exception as e:
            msgs.append("sol_ladybug: falhou (" + str(e) + ")")

    try:
        msgs.append(_gravar(os.path.join(destino, "ceu_ladybug.json"), _ceu_ladybug()))
    except Exception as e:
        msgs.append("ceu_ladybug: falhou (" + str(e) + ")")

    if resultados:
        if pontos and len(resultados) != len(pontos):
            return "ERRO: 'resultados' e 'pontos' têm tamanhos diferentes."
        nome = nome_resultado or "resultado"
        dados = {"nome": nome, "valores": [float(v) for v in resultados]}
        msgs.append(_gravar(os.path.join(destino, "resultado_" + nome + ".json"), dados))

    return "Gravado em " + destino + ":\n" + "\n".join(msgs)


# entradas opcionais que não foram criadas no componente viram None
for _nome in ["contexto", "pontos", "normais", "epw", "resultados", "nome_resultado", "geometria"]:
    if _nome not in globals():
        globals()[_nome] = None

log = executar()

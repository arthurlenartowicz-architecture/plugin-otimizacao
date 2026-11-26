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
  matriz_ceu     sky_mtx    (lista, opcional) saídas do LB Cumulative Sky Matrix
  malha_lb       Mesh       (opcional) saída "mesh" do LB Incident Radiation
  deslocamento   float      (opcional) _offset_dist_ usado no estudo do LB
  radiacao       float      (lista, opcional) LB Incident Radiation, céu com solo
  radiacao_sem_solo float   (lista, opcional) idem, com ground_ref_ = 0
  horas_sol      float      (lista, opcional) LB Direct Sun Hours
  vetores_sol    Vector3d   (lista, opcional) "vectors" do LB Sunpath
  hoys_sol       float      (lista, opcional) "hoys" do LB Sunpath
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


def _jsonavel(x, prof=0):
    """Converte o que der (listas, tuplas, números, dicts) para JSON."""
    if prof > 6:
        return str(x)
    if x is None or isinstance(x, (bool, int, float, str)):
        return x
    if isinstance(x, dict):
        return dict((str(k), _jsonavel(v, prof + 1)) for k, v in x.items())
    try:
        return [_jsonavel(v, prof + 1) for v in x]
    except TypeError:
        try:
            return float(x)
        except Exception:
            return str(x)


def _matriz_ladybug(mtx):
    """Grava o conteúdo de um sky_mtx do LB Cumulative Sky Matrix."""
    attrs = [a for a in dir(mtx) if not a.startswith("_")]
    saida = {"tipo": type(mtx).__name__, "atributos": attrs}
    for nome in ["high_density", "north", "ground_reflectance", "data",
                 "direct_values", "diffuse_values", "total_values"]:
        if nome in attrs:
            try:
                v = getattr(mtx, nome)
                if not callable(v):
                    saida[nome] = _jsonavel(v)
            except Exception as e:
                saida[nome] = "erro: " + str(e)
    return saida


def _sensores_da_malha(malha, k, desloc):
    """Centro, normal e área de cada face da malha de resultado do Ladybug."""
    m = _coagir(malha, "geometria")
    m.FaceNormals.ComputeFaceNormals()
    pts, nrm, areas = [], [], []
    for i in range(m.Faces.Count):
        f = m.Faces[i]
        c = m.Faces.GetFaceCenter(i)
        n = rg.Vector3d(m.FaceNormals[i])
        n.Unitize()
        a, b, cc = m.Vertices[f.A], m.Vertices[f.B], m.Vertices[f.C]
        area = rg.Vector3d.CrossProduct(rg.Vector3d(b - a), rg.Vector3d(cc - a)).Length / 2
        if f.IsQuad:
            d = m.Vertices[f.D]
            area += rg.Vector3d.CrossProduct(rg.Vector3d(cc - a), rg.Vector3d(d - a)).Length / 2
        pts.append([c.X * k, c.Y * k, c.Z * k])
        nrm.append([n.X, n.Y, n.Z])
        areas.append(area * k * k)
    return {"centros": pts, "normais": nrm, "areas": areas, "deslocamento": desloc}


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

    for i, mtx in enumerate(matriz_ceu or []):
        if mtx is None:
            continue
        try:
            d = _matriz_ladybug(mtx)
            nome = "matriz_ceu_ladybug_%d.json" % i
            msgs.append(_gravar(os.path.join(destino, nome), d))
        except Exception as e:
            msgs.append("matriz_ceu %d: falhou (%s)" % (i, e))

    if malha_lb is not None:
        desloc = float(deslocamento or 0.0) * k
        msgs.append(_gravar(os.path.join(destino, "sensores_lb.json"),
                            _sensores_da_malha(malha_lb, k, desloc)))
    for nome, valores in [("radiacao", radiacao), ("radiacao_sem_solo", radiacao_sem_solo),
                          ("horas_sol", horas_sol)]:
        if valores:
            dados = {"nome": nome, "valores": [float(v) for v in valores]}
            msgs.append(_gravar(os.path.join(destino, "resultado_" + nome + ".json"), dados))
    if vetores_sol:
        vs = []
        for v in vetores_sol:
            v = rg.Vector3d(_coagir(v, "vetor"))
            vs.append([v.X, v.Y, v.Z])
        dados = {"vetores": vs, "hoys": [float(h) for h in (hoys_sol or [])]}
        msgs.append(_gravar(os.path.join(destino, "sol_vetores_lb.json"), dados))

    if resultados:
        if pontos and len(resultados) != len(pontos):
            return "ERRO: 'resultados' e 'pontos' têm tamanhos diferentes."
        nome = nome_resultado or "resultado"
        dados = {"nome": nome, "valores": [float(v) for v in resultados]}
        msgs.append(_gravar(os.path.join(destino, "resultado_" + nome + ".json"), dados))

    return "Gravado em " + destino + ":\n" + "\n".join(msgs)


# entradas opcionais que não foram criadas no componente viram None
for _nome in ["contexto", "pontos", "normais", "epw", "resultados", "nome_resultado", "geometria",
              "matriz_ceu", "malha_lb", "deslocamento", "radiacao", "radiacao_sem_solo",
              "horas_sol", "vetores_sol", "hoys_sol"]:
    if _nome not in globals():
        globals()[_nome] = None

log = executar()

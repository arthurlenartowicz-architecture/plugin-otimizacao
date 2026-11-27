#! python 2
# -*- coding: utf-8 -*-
"""Validação da Etapa 2: roda os casos de testes/referencias/karamba/ no Karamba3D
pela API (KarambaCommon.Toolkit) e grava karamba_<caso>.json na mesma pasta.

Não cria nenhum componente do Karamba no canvas: o modelo é montado e analisado
aqui dentro. API conferida no Karamba3D 3.1.60921 (reflexão do KarambaCommon.dll
e exemplos em Examples/Scripts).

Como usar
  - no Grasshopper: componente GhPython criado por montar_karamba.py
    ENTRADAS: pasta (str, opcional), rodar (bool, ligue a um botão)   SAÍDA: log
  - ou direto no Rhino: _-RunPythonScript "<caminho>\\karamba_validar.py"
    (se der erro de licença, rode pelo Grasshopper, onde o Karamba já está carregado)

Unidades: geometria em m e forças em kN (base SI do Karamba); seção do tubo
no Karamba em cm (CircularHollow, padrão 11,43 × 0,4). Material: aço padrão
do Karamba (FemMaterial.defaultSteel, S235), igual ao ACO_S235 do nosso FEM.
"""
import io
import json
import os
import sys
import traceback

import clr

if sys.platform != "cli" or not hasattr(clr, "Reference"):
    raise RuntimeError("karamba_validar.py precisa do IronPython (componente GhPython do Grasshopper, "
                       "ou a 1ª linha '#! python 2' no Rhino). Use a definição validar_karamba.gh.")

RAIZ_PADRAO = r"C:\Users\PC\Documents\plugin-otimizacao"
CASO_CARGA = "LC0"
POSICOES = [0.0, 0.25, 0.5, 0.75, 1.0]   # pontos ao longo de cada barra (como o nosso FEM)


def _referenciar():
    """Carrega o KarambaCommon (já carregado se o Karamba abriu no Grasshopper)."""
    try:
        clr.AddReference("KarambaCommon")
        return "KarambaCommon já carregado"
    except Exception:
        pass
    base = os.path.join(os.environ["APPDATA"], "McNeel", "Rhinoceros", "packages", "8.0", "Karamba3D")
    versoes = sorted(os.listdir(base)) if os.path.isdir(base) else []
    import System
    if System.Environment.Version.Major >= 5:   # Rhino 8 em .NET 7/8
        alvos = ("net8.0-windows", "net7.0-windows", "net48")
    else:                                       # Rhino em .NET Framework
        alvos = ("net48", "net8.0-windows", "net7.0-windows")
    for v in reversed(versoes):
        for alvo in alvos:
            c = os.path.join(base, v, alvo, "KarambaCommon.dll")
            if os.path.isfile(c):
                clr.AddReferenceToFileAndPath(c)
                return c
    raise RuntimeError("KarambaCommon.dll não encontrado em " + base)


ORIGEM = _referenciar()

import System  # noqa: E402
from System import Guid  # noqa: E402
from System.Collections.Generic import List  # noqa: E402

import Karamba.Algorithms  # noqa: E402
import Karamba.Licenses  # noqa: E402
import Karamba.Results  # noqa: E402
import KarambaCommon  # noqa: E402
from Karamba.CrossSections import CroSec  # noqa: E402
from Karamba.Elements import BuilderElement  # noqa: E402
from Karamba.Geometry import Line3, Point3, Vector3  # noqa: E402
from Karamba.Loads import Load  # noqa: E402
from Karamba.Materials import FemMaterial  # noqa: E402
from Karamba.Supports import Support  # noqa: E402
from Karamba.Utilities import MessageLogger, UnitsConversionFactory  # noqa: E402


# ---------------------------------------------------------------- conversões
def _num(x):
    try:
        return float(x)
    except Exception:
        return str(x)


def _vet(v):
    return [float(v.X), float(v.Y), float(v.Z)]


def _aninhado(x, prof=0):
    """Listas .NET aninhadas (de Vector3/Point3/números) -> listas Python."""
    if x is None:
        return None
    if hasattr(x, "X") and hasattr(x, "Y") and hasattr(x, "Z"):
        return _vet(x)
    if isinstance(x, (int, float, bool, str)) or prof > 6:
        return _num(x) if not isinstance(x, str) else x
    try:
        return [_aninhado(v, prof + 1) for v in x]
    except TypeError:
        return _num(x)


def _lista(tipo, itens):
    lst = List[tipo]()
    for i in itens:
        lst.Add(i)
    return lst


def _ler(caminho):
    with io.open(caminho, encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------- modelo
def _montar(d, escala_secao, msgs):
    k3d = KarambaCommon.Toolkit()
    logger = MessageLogger()
    aco = FemMaterial.defaultSteel()
    sc = d["secoes"][0]
    cs = k3d.CroSec.CircularHollow(sc["D"] * escala_secao, sc["t"] * escala_secao, aco)

    pts = [Point3(x, y, z) for x, y, z in d["nos"]]
    elems = List[BuilderElement]()
    for trelica in (False, True):
        sel = [b for b in range(len(d["barras"])) if bool(d["trelica"][b]) == trelica]
        if not sel:
            continue
        linhas = _lista(Line3, [Line3(pts[d["barras"][b][0]], pts[d["barras"][b][1]]) for b in sel])
        ids = _lista(System.String, ["b%d" % b for b in sel])
        secoes = _lista(CroSec, [cs for _ in sel])
        nos_saida = clr.Reference[List[Point3]]()
        vigas = k3d.Part.LineToBeam(linhas, ids, secoes, logger, nos_saida, not trelica)
        for v in vigas:
            elems.Add(v)

    apoios = List[Support]()
    for g in d["apoios"]:
        cond = _lista(System.Boolean, [bool(c) for c in g["condicoes"]])
        for n in g["nos"]:
            apoios.Add(k3d.Support.Support(pts[n], cond))
    cargas = List[Load]()
    for c in d["cargas"]:
        cargas.Add(k3d.Load.PointLoad(pts[c["no"]], Vector3(*c["forca"]), Vector3(*c["momento"]),
                                      CASO_CARGA, False))

    info = clr.Reference[System.String]()
    massa = clr.Reference[System.Double]()
    cog = clr.Reference[Point3]()
    msg = clr.Reference[System.String]()
    aviso = clr.Reference[System.Boolean]()
    modelo = k3d.Model.AssembleModel(elems, apoios, cargas, info, massa, cog, msg, aviso,
                                     None, _lista(Point3, pts))
    if msg.Value:
        msgs.append("  Assemble: " + str(msg.Value).strip())
    return modelo, float(massa.Value), cs, aco


def _massa_esperada(d):
    m = 0.0
    for (i, j), sc in zip(d["barras"], d["secoes"]):
        a, b = d["nos"][i], d["nos"][j]
        L = sum((a[k] - b[k]) ** 2 for k in range(3)) ** 0.5
        m += d["material"]["rho"] * sc["A"] * L
    return m


def _analisar(d, msgs):
    esperada = _massa_esperada(d)
    # a seção do Karamba é em cm; se a massa sair 10⁴ vezes maior, tenta em m
    for escala, unidade in ((100.0, "cm"), (1.0, "m")):
        modelo, massa, cs, aco = _montar(d, escala, msgs)
        razao = massa / esperada if esperada else 0
        msgs.append("  seção em %s: massa Karamba %.2f kg, esperada %.2f (razão %.4f)"
                    % (unidade, massa, esperada, razao))
        if 0.9 < razao < 1.1:
            break
    saida = {"nome": d["nome"], "massa": massa, "unidade_secao": unidade,
             "versao_karamba": str(Karamba.Licenses.License.version()),
             "origem_dll": ORIGEM}
    try:
        saida["licenca"] = str(Karamba.Licenses.License.licenseType())
    except Exception as e:
        saida["licenca"] = "?" + str(e)
    try:
        ucf = UnitsConversionFactory.Conv()
        saida["unidades_base"] = {"comprimento": str(ucf.base_length.unit), "forca": str(ucf.base_force.unit)}
    except Exception as e:
        saida["unidades_base"] = str(e)
    saida["secao_karamba"] = dict((k, _num(getattr(cs, k))) for k in ("A", "Iyy", "Izz", "Ipp", "Ay", "Az"))
    saida["material_karamba"] = dict((k, _num(getattr(aco, k)())) for k in ("E", "G12", "G3", "gamma", "ft"))

    lcs = _lista(System.String, [CASO_CARGA])
    dmax, resultante, energia, modelo_a, aviso = Karamba.Algorithms.Analyze.solve(modelo, lcs)
    if aviso:
        msgs.append("  Analyze: " + str(aviso).strip())
    saida["desloc_max"] = _aninhado(dmax)
    saida["energia"] = _aninhado(energia)
    saida["resultante"] = _aninhado(resultante)
    saida["nos_karamba"] = [_vet(n.pos) for n in modelo_a.nodes]

    # deslocamentos nodais
    try:
        idx = _lista(System.Int32, range(modelo_a.nodes.Count))
        trans, rot, _, _, posicoes = Karamba.Results.NodeDisplacements.solve(modelo_a, CASO_CARGA, idx)
        saida["trans"] = _aninhado(trans)
        saida["rot"] = _aninhado(rot)
        saida["posicoes_nos"] = _aninhado(posicoes)
    except Exception as e:
        msgs.append("  NodeDisplacements falhou: " + str(e))

    # esforços nas barras (forças N Vy Vz e momentos Mt My Mz, eixos locais)
    ids = _lista(System.String, ["b%d" % b for b in range(len(d["barras"]))])
    try:
        forcas, momentos, _, _, inds = Karamba.Results.BeamForces.solve(
            modelo_a, ids, List[Guid](), CASO_CARGA, _lista(System.Double, POSICOES))
        saida["forcas"] = _aninhado(forcas)
        saida["momentos"] = _aninhado(momentos)
        saida["indices_elem"] = [None if i is None else int(i) for i in inds]
        saida["posicoes_barra"] = POSICOES
        try:
            saida["ids_elem"] = [None if i is None else str(modelo_a.elems[int(i)].id) for i in inds]
        except Exception as e:
            msgs.append("  ids dos elementos não lidos: " + str(e))
    except Exception as e:
        msgs.append("  BeamForces falhou: " + str(e))
        try:
            N, V, M = Karamba.Results.BeamResultantForces.solve(modelo_a, ids, CASO_CARGA, 100.0, 5)
            saida["N_resultante"] = _aninhado(N)
            saida["V_resultante"] = _aninhado(V)
            saida["M_resultante"] = _aninhado(M)
        except Exception as e2:
            msgs.append("  BeamResultantForces falhou: " + str(e2))

    # flambagem (mesmos parâmetros dos exemplos do Karamba)
    try:
        fatores, _, msg = Karamba.Algorithms.Buckling.solve(modelo_a, 1, 3, 300, 1.0e-7, 1)
        saida["flambagem"] = _aninhado(fatores)
        if msg:
            msgs.append("  Buckling: " + str(msg).strip())
    except Exception as e:
        msgs.append("  Buckling falhou: " + str(e))
    return saida


def executar(pasta_kr):
    msgs = ["Karamba: " + ORIGEM]
    entradas = sorted(n for n in os.listdir(pasta_kr) if n.startswith("entrada_") and n.endswith(".json"))
    if not entradas:
        return "Nenhum entrada_*.json em " + pasta_kr
    for nome_arq in entradas:
        d = _ler(os.path.join(pasta_kr, nome_arq))
        msgs.append("%s: %d nós, %d barras" % (d["nome"], len(d["nos"]), len(d["barras"])))
        try:
            saida = _analisar(d, msgs)
            destino = os.path.join(pasta_kr, "karamba_%s.json" % d["nome"])
            with open(destino, "w") as f:
                json.dump(saida, f)
            msgs.append("  gravado %s (desloc. máx. %s, flambagem %s)"
                        % (os.path.basename(destino), saida.get("desloc_max"), saida.get("flambagem")))
        except Exception:
            msgs.append("  ERRO:\n" + traceback.format_exc())
    return "\n".join(msgs)


def _pasta_padrao():
    try:
        raiz = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
        if os.path.isfile(os.path.join(raiz, "CLAUDE.md")):
            return os.path.join(raiz, "testes", "referencias", "karamba")
    except NameError:
        pass
    return os.path.join(RAIZ_PADRAO, "testes", "referencias", "karamba")


if "rodar" in globals():          # dentro do componente do Grasshopper
    if not rodar:
        log = "Aperte o botão 'rodar'."
    else:
        log = executar(pasta if globals().get("pasta") else _pasta_padrao())
else:                              # rodando direto no Rhino
    print(executar(_pasta_padrao()))

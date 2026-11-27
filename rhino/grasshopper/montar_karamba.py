# -*- coding: utf-8 -*-
"""Monta no Grasshopper a definição (pequena) de validação contra o Karamba.

Como usar (uma vez): no Rhino 8, digite na linha de comando

    _-RunPythonScript "C:\\Users\\PC\\Documents\\plugin-otimizacao\\rhino\\grasshopper\\montar_karamba.py"

Cria um documento novo com UM componente GhPython (código de karamba_validar.py),
um painel com a pasta dos casos, um botão "rodar" e um painel de log, e salva
rhino/grasshopper/validar_karamba.gh. Nenhum componente do Karamba é colocado
no canvas e nada é calculado na montagem: o modelo só é montado e analisado
(pela API do Karamba) quando você aperta o botão.
"""
import os

import Rhino
import System
import System.Drawing as sd

RAIZ_PADRAO = r"C:\Users\PC\Documents\plugin-otimizacao"
try:
    RAIZ = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
except NameError:
    RAIZ = RAIZ_PADRAO
if not os.path.isfile(os.path.join(RAIZ, "CLAUDE.md")):
    RAIZ = RAIZ_PADRAO

PASTA_GH = os.path.join(RAIZ, "rhino", "grasshopper")
CODIGO = os.path.join(PASTA_GH, "karamba_validar.py")
SAIDA_GH = os.path.join(PASTA_GH, "validar_karamba.gh")
PASTA_KR = os.path.join(RAIZ, "testes", "referencias", "karamba")


def colocar(doc, obj, x, y):
    obj.CreateAttributes()
    obj.Attributes.Pivot = sd.PointF(x, y)
    doc.AddObject(obj, False)
    return obj


def painel(gh, doc, texto, apelido, x, y, largura, altura=20, multilinha=False):
    p = gh.Kernel.Special.GH_Panel()
    p.NickName = apelido
    p.UserText = texto
    p.Properties.Multiline = multilinha
    colocar(doc, p, x, y)
    p.Attributes.Bounds = sd.RectangleF(x, y, largura, altura)
    return p


def criar_ghpython(gh, doc, x, y):
    proxy = None
    for px in gh.Instances.ComponentServer.ObjectProxies:
        if px.Desc.Name == "GhPython Script":
            proxy = px
            break
    if proxy is None:
        raise RuntimeError("Componente 'GhPython Script' não encontrado no Grasshopper.")
    comp = gh.Instances.ComponentServer.EmitObject(proxy.Guid)
    colocar(doc, comp, x, y)
    lado_in = gh.Kernel.GH_ParameterSide.Input
    lado_out = gh.Kernel.GH_ParameterSide.Output
    for p in list(comp.Params.Input):
        comp.Params.UnregisterInputParameter(p, True)
    for i, (nome, desc) in enumerate([("pasta", "pasta testes/referencias/karamba"),
                                      ("rodar", "ligue a um botão")]):
        p = comp.CreateParameter(lado_in, i)
        p.Name = nome
        p.NickName = nome
        p.Description = desc
        p.Optional = True
        p.Access = gh.Kernel.GH_ParamAccess.item
        comp.Params.RegisterInputParam(p, i)
    for p in list(comp.Params.Output):
        if p.NickName != "out":
            comp.Params.UnregisterOutputParameter(p, True)
    p = comp.CreateParameter(lado_out, comp.Params.Output.Count)
    p.Name = "log"
    p.NickName = "log"
    p.Description = "O que foi calculado e gravado"
    comp.Params.RegisterOutputParam(p, comp.Params.Output.Count)
    comp.Params.OnParametersChanged()
    comp.VariableParameterMaintenance()
    codigo = System.IO.File.ReadAllText(CODIGO, System.Text.Encoding.UTF8)  # acentos certos no IronPython
    try:
        comp.Code = codigo
    except Exception as e:
        print("Não consegui colocar o código no componente (" + str(e) + "). "
              "Abra o componente e cole o conteúdo de " + CODIGO)
    comp.NickName = "Validar Karamba"
    comp.Attributes.ExpireLayout()
    return comp


def montar():
    gh_plugin = Rhino.RhinoApp.GetPlugInObject("Grasshopper")
    if gh_plugin is None:
        raise RuntimeError("Grasshopper não encontrado.")
    gh_plugin.ShowEditor()
    import Grasshopper as gh

    doc = gh.Kernel.GH_Document()
    gh.Instances.DocumentServer.AddDocument(doc)
    gh.Instances.ActiveCanvas.Document = doc

    comp = criar_ghpython(gh, doc, 520, 120)
    entrada = dict((p.NickName, p) for p in comp.Params.Input)
    p_pasta = painel(gh, doc, PASTA_KR, "pasta", 100, 100, 380)
    botao = colocar(doc, gh.Kernel.Special.GH_ButtonObject(), 380, 150)
    botao.NickName = "rodar"
    entrada["pasta"].AddSource(p_pasta)
    entrada["rodar"].AddSource(botao)

    log = painel(gh, doc, "", "log", 720, 100, 520, 260, True)
    log.AddSource(comp.Params.Output[comp.Params.Output.Count - 1])
    nota = painel(gh, doc,
                  "Aperte 'rodar': monta e analisa os casos entrada_*.json no Karamba (API) e grava "
                  "karamba_<caso>.json na mesma pasta. Depois rode no PowerShell: "
                  ".venv\\Scripts\\python.exe -m pytest testes\\test_karamba.py -v -s",
                  "LEIA", 100, 200, 380, 80, True)
    gh.Instances.ActiveCanvas.Refresh()
    gh.Kernel.GH_DocumentIO(doc).SaveQuiet(SAIDA_GH)
    print("Definição montada e salva em " + SAIDA_GH)
    return nota


montar()

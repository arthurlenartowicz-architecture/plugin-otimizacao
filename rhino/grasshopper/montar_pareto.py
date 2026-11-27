# -*- coding: utf-8 -*-
"""Monta no Grasshopper a definição (pequena) que mostra a frente de Pareto (Etapa 3).

Como usar (uma vez): no Rhino 8, digite na linha de comando

    _-RunPythonScript "C:\\Users\\PC\\Documents\\plugin-otimizacao\\rhino\\grasshopper\\montar_pareto.py"

Cria um documento novo com UM componente GhPython (que carrega pareto_rhino.py do
disco a cada execução), um painel com o caso, um botão "rodar", um slider de
solução, um toggle "so_representantes" e painéis de log, parâmetros, objetivos e
tabela, e salva rhino/grasshopper/pareto.gh. Nada é calculado na montagem.
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
CODIGO = os.path.join(PASTA_GH, "pareto_rhino.py")
SAIDA_GH = os.path.join(PASTA_GH, "pareto.gh")
ENTRADAS = [("arquivo", "caminho do resultado.json (opcional)"),
            ("caso", "trelica ou sala"),
            ("rodar", "botão: roda a otimização (trava o GH até acabar)"),
            ("solucao", "índice da solução"),
            ("so_representantes", "só um por cluster"),
            ("espacamento", "passo entre soluções em todas (m, 0 = automático)"),
            ("aplicar", "botão: põe a solução escolhida nos sliders do Otimizar")]
SAIDAS = ["log", "parametros", "valores", "objetivos", "geometria", "vidros", "todas", "tabela"]


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
    for i, (nome, desc) in enumerate(ENTRADAS):
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
    for nome in SAIDAS:
        p = comp.CreateParameter(lado_out, comp.Params.Output.Count)
        p.Name = nome
        p.NickName = nome
        p.Description = nome
        comp.Params.RegisterOutputParam(p, comp.Params.Output.Count)
    comp.Params.OnParametersChanged()
    comp.VariableParameterMaintenance()
    # o componente lê pareto_rhino.py do disco a cada execução: correções no arquivo
    # valem sem montar a definição de novo
    codigo = (
        "# Carrega o código atual de pareto_rhino.py (não edite aqui; edite o arquivo)\n"
        "import io\n"
        "_arq = r\"" + CODIGO + "\"\n"
        "_src = io.open(_arq, encoding='utf-8').read().replace('# -*- coding: utf-8 -*-', '')\n"
        "exec(compile(_src, _arq, 'exec'))\n"
    )
    try:
        comp.Code = codigo
    except Exception as e:
        print("Não consegui colocar o código no componente (" + str(e) + "). "
              "Abra o componente e cole o conteúdo de " + CODIGO)
    comp.NickName = "Pareto"
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
    saida = dict((p.NickName, p) for p in comp.Params.Output)
    entrada["caso"].AddSource(painel(gh, doc, "trelica", "caso", 300, 100, 120))
    botao = colocar(doc, gh.Kernel.Special.GH_ButtonObject(), 340, 140)
    botao.NickName = "rodar"
    entrada["rodar"].AddSource(botao)
    sl = gh.Kernel.Special.GH_NumberSlider()
    sl.Slider.Minimum = System.Decimal(0)
    sl.Slider.Maximum = System.Decimal(199)
    sl.Slider.DecimalPlaces = 0
    sl.Slider.Value = System.Decimal(0)
    sl.NickName = "solucao"
    colocar(doc, sl, 220, 180)
    entrada["solucao"].AddSource(sl)
    ap = colocar(doc, gh.Kernel.Special.GH_ButtonObject(), 340, 260)
    ap.NickName = "aplicar"
    entrada["aplicar"].AddSource(ap)
    tg = colocar(doc, gh.Kernel.Special.GH_BooleanToggle(), 300, 220)
    tg.NickName = "so_representantes"
    entrada["so_representantes"].AddSource(tg)
    log = painel(gh, doc, "", "log", 760, 60, 420, 110, True)
    log.AddSource(saida["log"])
    for k, nome in enumerate(["parametros", "objetivos"]):
        pn = painel(gh, doc, "", nome, 760, 180 + 130 * k, 300, 120, True)
        pn.AddSource(saida[nome])
    tab = painel(gh, doc, "", "tabela", 760, 450, 560, 220, True)
    tab.AddSource(saida["tabela"])
    nota = painel(gh, doc,
                  "Rode antes (PowerShell): .venv\\Scripts\\python.exe scripts\\otimizar_exemplo.py trelica "
                  "(ou sala), ou aperte 'rodar'. O slider escolhe a solução; 'geometria' é a escolhida, "
                  "'todas' mostra a frente lado a lado. 'aplicar' põe a solução nos sliders do Otimizar.",
                  "LEIA", 100, 280, 380, 90, True)
    gh.Instances.ActiveCanvas.Refresh()
    gh.Kernel.GH_DocumentIO(doc).SaveQuiet(SAIDA_GH)
    print("Definição montada e salva em " + SAIDA_GH)
    return nota


montar()

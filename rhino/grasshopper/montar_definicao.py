# -*- coding: utf-8 -*-
"""Monta no Grasshopper a definição de exportação de referências.

Como usar (uma vez): no Rhino 8, digite na linha de comando

    _-RunPythonScript "C:\\Users\\PC\\Documents\\plugin-otimizacao\\rhino\\grasshopper\\montar_definicao.py"

O script abre o Grasshopper, cria um documento novo com:
  - componente GhPython com o código de exportar_referencias.py
  - painéis já preenchidos (pasta, caso, epw, nome_resultado)
  - botão "exportar" e painel de log
e salva como rhino/grasshopper/exportar_referencias.gh. Depois é só abrir
esse .gh, ligar geometria/sensores quando precisar e clicar no botão.
"""
import os

import Rhino
import System
import System.Drawing as sd

# ---------------------------------------------------------------- caminhos
RAIZ_PADRAO = r"C:\Users\PC\Documents\plugin-otimizacao"
try:
    RAIZ = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
except NameError:
    RAIZ = RAIZ_PADRAO
if not os.path.isfile(os.path.join(RAIZ, "CLAUDE.md")):
    RAIZ = RAIZ_PADRAO

PASTA_GH = os.path.join(RAIZ, "rhino", "grasshopper")
CODIGO = os.path.join(PASTA_GH, "exportar_referencias.py")
SAIDA_GH = os.path.join(PASTA_GH, "exportar_referencias.gh")
PASTA_REF = os.path.join(RAIZ, "testes", "referencias")
CASO_PADRAO = "curitiba"

# (nome, lista?, descrição)
ENTRADAS = [
    ("pasta", False, "Pasta testes/referencias do projeto"),
    ("caso", False, "Nome do caso (subpasta)"),
    ("geometria", True, "Breps/Meshes do estudo"),
    ("contexto", True, "Breps/Meshes do entorno (opcional)"),
    ("pontos", True, "Pontos dos sensores (opcional)"),
    ("normais", True, "Normais dos sensores (opcional)"),
    ("epw", False, "Caminho do arquivo .epw (opcional)"),
    ("resultados", True, "Resultado do Ladybug por sensor (opcional)"),
    ("nome_resultado", False, "Nome do resultado, ex.: radiacao_kwh_m2"),
    ("matriz_ceu", True, "sky_mtx do LB Cumulative Sky Matrix (opcional)"),
    ("exportar", False, "Ligue a um botão"),
]


def achar_epw():
    pasta = os.path.join(RAIZ, "data", "epw")
    for raiz, _, arquivos in os.walk(pasta):
        for a in sorted(arquivos):
            if a.lower().endswith(".epw"):
                return os.path.join(raiz, a)
    return ""


# ---------------------------------------------------------------- Grasshopper
def abrir_grasshopper():
    gh_plugin = Rhino.RhinoApp.GetPlugInObject("Grasshopper")
    if gh_plugin is None:
        raise RuntimeError("Grasshopper não encontrado.")
    gh_plugin.ShowEditor()
    import Grasshopper
    return Grasshopper


def novo_documento(gh):
    doc = gh.Kernel.GH_Document()
    gh.Instances.DocumentServer.AddDocument(doc)
    gh.Instances.ActiveCanvas.Document = doc
    return doc


def colocar(doc, obj, x, y):
    obj.CreateAttributes()
    obj.Attributes.Pivot = sd.PointF(x, y)
    doc.AddObject(obj, False)
    return obj


def painel(gh, doc, texto, apelido, x, y, largura=320):
    p = gh.Kernel.Special.GH_Panel()
    p.NickName = apelido
    p.UserText = texto
    p.Properties.Multiline = False
    colocar(doc, p, x, y)
    p.Attributes.Bounds = sd.RectangleF(x, y, largura, 20)
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

    # troca as entradas padrão (x, y) pelas nossas
    for p in list(comp.Params.Input):
        comp.Params.UnregisterInputParameter(p, True)
    for i, (nome, lista, desc) in enumerate(ENTRADAS):
        p = comp.CreateParameter(lado_in, i)
        p.Name = nome
        p.NickName = nome
        p.Description = desc
        p.Optional = True
        p.Access = gh.Kernel.GH_ParamAccess.list if lista else gh.Kernel.GH_ParamAccess.item
        comp.Params.RegisterInputParam(p, i)

    # saídas: mantém "out" (mensagens do print) e troca o resto por "log"
    for p in list(comp.Params.Output):
        if p.NickName != "out":
            comp.Params.UnregisterOutputParameter(p, True)
    p = comp.CreateParameter(lado_out, comp.Params.Output.Count)
    p.Name = "log"
    p.NickName = "log"
    p.Description = "Resumo do que foi gravado"
    comp.Params.RegisterOutputParam(p, comp.Params.Output.Count)

    comp.Params.OnParametersChanged()
    comp.VariableParameterMaintenance()

    codigo = System.IO.File.ReadAllText(CODIGO, System.Text.Encoding.UTF8)  # acentos certos no IronPython
    try:
        comp.Code = codigo
    except Exception as e:
        print("Não consegui colocar o código no componente (" + str(e) + "). "
              "Abra o componente e cole o conteúdo de " + CODIGO)
    comp.NickName = "Exportar referencias"
    comp.Attributes.ExpireLayout()
    return comp


def criar_por_nome(gh, doc, nome, x, y):
    """Coloca um componente (ou user object, como os do Ladybug) pelo nome."""
    for px in gh.Instances.ComponentServer.ObjectProxies:
        if px.Desc.Name == nome:
            obj = gh.Instances.ComponentServer.EmitObject(px.Guid)
            if obj is not None:
                return colocar(doc, obj, x, y)
    print("Aviso: componente '" + nome + "' não encontrado (Ladybug instalado?).")
    return None


def achar_param(lista, apelido):
    alvo = apelido.strip("_").lower()
    for p in lista:
        for n in (p.NickName, p.Name):
            if n and n.strip("_").lower() == alvo:
                return p
    print("Aviso: parâmetro '" + apelido + "' não encontrado.")
    return None


def ligar(destino, origem):
    if destino is not None and origem is not None:
        destino.AddSource(origem)


def montar_ladybug(gh, doc, p_epw, entrada_matriz):
    """LB Import EPW -> 2x LB Cumulative Sky Matrix (Tregenza e Reinhart) -> matriz_ceu."""
    imp = criar_por_nome(gh, doc, "LB Import EPW", 150, 560)
    if imp is None:
        return
    ligar(achar_param(imp.Params.Input, "_epw_file"), p_epw)
    loc = achar_param(imp.Params.Output, "location")
    dni = achar_param(imp.Params.Output, "direct_normal_rad")
    dhi = achar_param(imp.Params.Output, "diffuse_horizontal_rad")

    for i, alta in enumerate([False, True]):
        mtx = criar_por_nome(gh, doc, "LB Cumulative Sky Matrix", 420, 560 + 160 * i)
        if mtx is None:
            return
        ligar(achar_param(mtx.Params.Input, "_location"), loc)
        ligar(achar_param(mtx.Params.Input, "_direct_rad"), dni)
        ligar(achar_param(mtx.Params.Input, "_diffuse_rad"), dhi)
        if alta:
            t = colocar(doc, gh.Kernel.Special.GH_BooleanToggle(), 250, 720)
            t.Value = True
            t.NickName = "Reinhart"
            ligar(achar_param(mtx.Params.Input, "high_density_"), t)
        ligar(entrada_matriz, achar_param(mtx.Params.Output, "sky_mtx"))


def montar():
    gh = abrir_grasshopper()
    doc = novo_documento(gh)

    comp = criar_ghpython(gh, doc, 600, 200)
    entrada = dict((p.NickName, p) for p in comp.Params.Input)

    y0, dy = 120, 40
    p_pasta = painel(gh, doc, PASTA_REF, "pasta", 150, y0)
    p_caso = painel(gh, doc, CASO_PADRAO, "caso", 150, y0 + dy, 160)
    p_epw = painel(gh, doc, achar_epw(), "epw", 150, y0 + 2 * dy)
    p_nome = painel(gh, doc, "radiacao_kwh_m2", "nome_resultado", 150, y0 + 3 * dy, 160)
    botao = colocar(doc, gh.Kernel.Special.GH_ButtonObject(), 380, y0 + 4 * dy)
    botao.NickName = "exportar"

    entrada["pasta"].AddSource(p_pasta)
    entrada["caso"].AddSource(p_caso)
    entrada["epw"].AddSource(p_epw)
    entrada["nome_resultado"].AddSource(p_nome)
    entrada["exportar"].AddSource(botao)

    montar_ladybug(gh, doc, p_epw, entrada["matriz_ceu"])

    log = painel(gh, doc, "", "log", 850, 200, 360)
    log.Attributes.Bounds = sd.RectangleF(850, 200, 360, 160)
    log.Properties.Multiline = True
    saida = dict((p.NickName, p) for p in comp.Params.Output)
    log.AddSource(saida["log"])

    nota = painel(
        gh, doc,
        "Ligue aqui quando precisar: geometria, contexto, pontos, normais "
        "(ex.: LB Generate Point Grid) e resultados (ex.: LB Incident Radiation).",
        "LEIA", 150, 420, 420,
    )
    nota.Properties.Multiline = True
    nota.Attributes.Bounds = sd.RectangleF(150, 420, 420, 60)

    doc.NewSolution(False)
    gh.Instances.ActiveCanvas.Refresh()

    io = gh.Kernel.GH_DocumentIO(doc)
    io.SaveQuiet(SAIDA_GH)
    print("Definição montada e salva em " + SAIDA_GH)


montar()

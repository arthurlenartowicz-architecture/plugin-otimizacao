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
    ("malha_lb", False, "mesh do LB Incident Radiation"),
    ("deslocamento", False, "_offset_dist_ do estudo"),
    ("radiacao", True, "results do LB Incident Radiation (solo 0,2)"),
    ("radiacao_sem_solo", True, "results do LB Incident Radiation (solo 0)"),
    ("horas_sol", True, "results do LB Direct Sun Hours"),
    ("vetores_sol", True, "vectors do LB Sunpath"),
    ("hoys_sol", True, "hoys do LB Sunpath"),
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
    lb = {"loc": None, "mtx": []}
    imp = criar_por_nome(gh, doc, "LB Import EPW", 150, 560)
    if imp is None:
        return lb
    ligar(achar_param(imp.Params.Input, "_epw_file"), p_epw)
    loc = achar_param(imp.Params.Output, "location")
    dni = achar_param(imp.Params.Output, "direct_normal_rad")
    dhi = achar_param(imp.Params.Output, "diffuse_horizontal_rad")
    lb["loc"] = loc

    # Tregenza, Reinhart e Tregenza sem solo (este último só para o estudo da 1b)
    for i, (alta, sem_solo) in enumerate([(False, False), (True, False), (False, True)]):
        mtx = criar_por_nome(gh, doc, "LB Cumulative Sky Matrix", 420, 560 + 160 * i)
        if mtx is None:
            return lb
        ligar(achar_param(mtx.Params.Input, "_location"), loc)
        ligar(achar_param(mtx.Params.Input, "_direct_rad"), dni)
        ligar(achar_param(mtx.Params.Input, "_diffuse_rad"), dhi)
        if alta:
            t = colocar(doc, gh.Kernel.Special.GH_BooleanToggle(), 250, 720)
            t.Value = True
            t.NickName = "Reinhart"
            ligar(achar_param(mtx.Params.Input, "high_density_"), t)
        saida = achar_param(mtx.Params.Output, "sky_mtx")
        if sem_solo:
            p0 = painel(gh, doc, "0", "ground_ref", 250, 880, 60)
            ligar(achar_param(mtx.Params.Input, "ground_ref_"), p0)
        else:
            ligar(entrada_matriz, saida)
        lb["mtx"].append(saida)
    return lb


def montar_estudo(gh, doc, lb, entrada):
    """Cena de teste da 1b: prédio + piso (analisados) e torre ao norte (contexto)."""
    import Rhino.Geometry as rg

    if lb["loc"] is None or len(lb["mtx"]) < 3:
        print("Aviso: estudo da 1b não montado (faltam componentes do Ladybug).")
        return
    # dimensões em metros convertidas para a unidade do modelo
    k = Rhino.RhinoMath.UnitScale(Rhino.UnitSystem.Meters, Rhino.RhinoDoc.ActiveDoc.ModelUnitSystem)

    def bloco(x0, y0, z0, x1, y1, z1):
        bb = rg.BoundingBox(rg.Point3d(x0 * k, y0 * k, z0 * k), rg.Point3d(x1 * k, y1 * k, z1 * k))
        return rg.Box(bb).ToBrep()

    piso = rg.PlaneSurface(rg.Plane.WorldXY, rg.Interval(-30 * k, 30 * k),
                           rg.Interval(-30 * k, 30 * k)).ToBrep()

    def param_brep(breps, apelido, x, y):
        p = gh.Kernel.Parameters.Param_Brep()
        p.NickName = apelido
        for b in breps:
            p.PersistentData.Append(gh.Kernel.Types.GH_Brep(b))
        return colocar(doc, p, x, y)

    x0, y0 = 150, 1100
    geo = param_brep([bloco(-5, -5, 0, 5, 5, 20), piso], "geometria", x0, y0)
    ctx = param_brep([bloco(-15, 15, 0, 15, 25, 30)], "contexto", x0, y0 + 50)
    grade = painel(gh, doc, "2", "grid_size (m)", x0, y0 + 100, 60)
    desloc = painel(gh, doc, "0.1", "offset (m)", x0, y0 + 140, 60)
    rodar = colocar(doc, gh.Kernel.Special.GH_BooleanToggle(), x0, y0 + 180)
    rodar.NickName = "rodar LB"
    rodar.Value = True

    ligar(entrada["geometria"], geo)
    ligar(entrada["contexto"], ctx)
    ligar(entrada["deslocamento"], desloc)

    def estudo(nome, x, y, extra):
        c = criar_por_nome(gh, doc, nome, x, y)
        if c is None:
            return None
        ligar(achar_param(c.Params.Input, "_geometry"), geo)
        ligar(achar_param(c.Params.Input, "context_"), ctx)
        ligar(achar_param(c.Params.Input, "_grid_size"), grade)
        ligar(achar_param(c.Params.Input, "_offset_dist_"), desloc)
        ligar(achar_param(c.Params.Input, "_run"), rodar)
        for nome_in, origem in extra:
            ligar(achar_param(c.Params.Input, nome_in), origem)
        return c

    rad = estudo("LB Incident Radiation", 480, y0, [("_sky_mtx", lb["mtx"][0])])
    rad0 = estudo("LB Incident Radiation", 480, y0 + 220, [("_sky_mtx", lb["mtx"][2])])
    if rad is not None:
        ligar(entrada["malha_lb"], achar_param(rad.Params.Output, "mesh"))
        ligar(entrada["radiacao"], achar_param(rad.Params.Output, "results"))
    if rad0 is not None:
        ligar(entrada["radiacao_sem_solo"], achar_param(rad0.Params.Output, "results"))

    # sol: ano inteiro, de hora em hora
    periodo = criar_por_nome(gh, doc, "LB Analysis Period", x0, y0 + 260)
    sp = criar_por_nome(gh, doc, "LB SunPath", 330, y0 + 260)
    if sp is None:
        sp = criar_por_nome(gh, doc, "LB Sunpath", 330, y0 + 260)
    if periodo is not None and sp is not None:
        ligar(achar_param(sp.Params.Input, "_location"), lb["loc"])
        ligar(achar_param(sp.Params.Input, "hoys_"), achar_param(periodo.Params.Output, "hoys"))
        vets = achar_param(sp.Params.Output, "vectors")
        ligar(entrada["vetores_sol"], vets)
        ligar(entrada["hoys_sol"], achar_param(sp.Params.Output, "hoys"))
        hs = estudo("LB Direct Sun Hours", 480, y0 + 440, [("_vectors", vets)])
        if hs is not None:
            ligar(entrada["horas_sol"], achar_param(hs.Params.Output, "results"))


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

    lb = montar_ladybug(gh, doc, p_epw, entrada["matriz_ceu"])
    montar_estudo(gh, doc, lb, entrada)

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

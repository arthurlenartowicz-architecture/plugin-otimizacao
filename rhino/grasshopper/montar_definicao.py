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
    ("modelo_hb", False, "HB Model da sala de teste (1c)"),
    ("hb_da", True, "DA do HB Annual Daylight"),
    ("hb_udi", True, "UDI do HB Annual Daylight"),
    ("hb_udi_baixo", True, "UDI_low do HB Annual Daylight"),
    ("hb_udi_alto", True, "UDI_up do HB Annual Daylight"),
    ("hb_df", True, "results do HB Daylight Factor"),
    ("hb_resultados", False, "pasta results do HB Annual Daylight"),
    ("modelo_hb_ofusc", False, "HB Model com a grade de vistas (1e)"),
    ("hb_ga", True, "GA do HB Imageless Annual Glare"),
    ("hb_ofusc_resultados", False, "pasta results do HB Imageless Annual Glare"),
    ("karamba", True, "JSON dos casos do Karamba (Empacotar Karamba)"),
    ("exportar", False, "Ligue a um botão"),
]
CASOS_KARAMBA = ("portico", "trelica")
CODIGO_CASO_KR = os.path.join(PASTA_GH, "caso_karamba.py")
CODIGO_PACOTE_KR = os.path.join(PASTA_GH, "empacotar_karamba.py")
ENTRADAS_CASO_KR = [("arquivo", False, "entrada_<nome>.json")]
SAIDAS_CASO_KR = ("nome", "nos", "vigas", "trelicas", "apoios", "condicoes", "pontos_carga",
                  "forcas", "momentos", "info")
ENTRADAS_PACOTE_KR = [(n, "arvore", d) for n, d in [
    ("nome", "nome do caso"), ("massa", "Mass do Assemble Model"), ("desloc_max", "Disp do Analyze"),
    ("energia", "energia de deformação do Analyze"), ("trans", "Trans do Nodal Displacements"),
    ("rot", "Rot do Nodal Displacements"), ("N", "N do Beam Forces"), ("V", "V do Beam Forces"),
    ("M", "M do Beam Forces"), ("flambagem", "BLFacs do Buckling Modes"), ("modelo", "Model")]]


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


def criar_ghpython(gh, doc, x, y, entradas=None, saidas=("log",), codigo_arq=None,
                   apelido="Exportar referencias"):
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
    acesso = gh.Kernel.GH_ParamAccess
    for i, (nome, lista, desc) in enumerate(entradas or ENTRADAS):
        p = comp.CreateParameter(lado_in, i)
        p.Name = nome
        p.NickName = nome
        p.Description = desc
        p.Optional = True
        p.Access = acesso.tree if lista == "arvore" else (acesso.list if lista else acesso.item)
        comp.Params.RegisterInputParam(p, i)

    # saídas: mantém "out" (mensagens do print) e troca o resto pelas nossas
    for p in list(comp.Params.Output):
        if p.NickName != "out":
            comp.Params.UnregisterOutputParameter(p, True)
    for nome in saidas:
        p = comp.CreateParameter(lado_out, comp.Params.Output.Count)
        p.Name = nome
        p.NickName = nome
        p.Description = nome
        comp.Params.RegisterOutputParam(p, comp.Params.Output.Count)

    comp.Params.OnParametersChanged()
    comp.VariableParameterMaintenance()

    codigo_arq = codigo_arq or CODIGO
    codigo = System.IO.File.ReadAllText(codigo_arq, System.Text.Encoding.UTF8)  # acentos certos no IronPython
    try:
        comp.Code = codigo
    except Exception as e:
        print("Não consegui colocar o código no componente (" + str(e) + "). "
              "Abra o componente e cole o conteúdo de " + codigo_arq)
    comp.NickName = apelido
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


def montar_honeybee(gh, doc, p_epw, entrada):
    """Sala de teste da 1c: 6 x 8 x 3 m, janelas 40% em todas as paredes,
    materiais padrão do Honeybee, sensores 0,5 m a 0,8 m do piso."""
    import Rhino.Geometry as rg

    k = Rhino.RhinoMath.UnitScale(Rhino.UnitSystem.Meters, Rhino.RhinoDoc.ActiveDoc.ModelUnitSystem)
    x0, y0 = 150, 1900
    bb = rg.BoundingBox(rg.Point3d(100 * k, 0, 0), rg.Point3d(106 * k, 8 * k, 3 * k))
    sala = gh.Kernel.Parameters.Param_Brep()
    sala.NickName = "sala HB"
    sala.PersistentData.Append(gh.Kernel.Types.GH_Brep(rg.Box(bb).ToBrep()))
    colocar(doc, sala, x0, y0)

    room = criar_por_nome(gh, doc, "HB Room from Solid", x0 + 150, y0)
    janelas = criar_por_nome(gh, doc, "HB Apertures by Ratio", x0 + 330, y0)
    grade = criar_por_nome(gh, doc, "HB Sensor Grid from Rooms", x0 + 510, y0 + 120)
    modelo = criar_por_nome(gh, doc, "HB Model", x0 + 700, y0)
    atribuir = criar_por_nome(gh, doc, "HB Assign Grids and Views", x0 + 830, y0 + 60)
    wea = criar_por_nome(gh, doc, "HB Wea From EPW", x0 + 510, y0 + 260)
    anual = criar_por_nome(gh, doc, "HB Annual Daylight", x0 + 1000, y0)
    df = criar_por_nome(gh, doc, "HB Daylight Factor", x0 + 1000, y0 + 260)
    if None in (room, janelas, grade, modelo, atribuir, wea, anual, df):
        print("Aviso: estudo do Honeybee incompleto; ligue o que faltar à mão.")

    p_ratio = painel(gh, doc, "0.4", "razão janela", x0 + 150, y0 + 80, 60)
    p_grade = painel(gh, doc, "0.5", "grade (m)", x0 + 330, y0 + 160, 60)
    p_altura = painel(gh, doc, "0.8", "altura (m)", x0 + 330, y0 + 200, 60)
    p_par_anual = painel(gh, doc, "-ab 5 -ad 20000 -lw 2e-05", "radiance_par anual",
                         x0 + 700, y0 + 120, 220)
    p_par_df = painel(gh, doc, "-ab 5 -ad 10000 -as 2048 -ar 256 -aa 0.05 -lw 2e-05",
                      "radiance_par DF", x0 + 700, y0 + 320, 300)
    rodar = colocar(doc, gh.Kernel.Special.GH_BooleanToggle(), x0 + 700, y0 + 200)
    rodar.NickName = "rodar HB"
    rodar.Value = True

    def entra(comp, nome, origem):
        if comp is not None:
            ligar(achar_param(comp.Params.Input, nome), origem)

    def sai(comp, *nomes):
        """Saída pelo primeiro nome que existir (as versões do HB variam)."""
        if comp is None:
            return None
        for n in nomes:
            for p in comp.Params.Output:
                if n.strip("_").lower() in (p.NickName.strip("_").lower(), p.Name.strip("_").lower()):
                    return p
        print("Aviso: saída " + "/".join(nomes) + " não encontrada em " + comp.Name)
        return None

    entra(room, "_solid", sala)
    entra(janelas, "_hb_objs", sai(room, "room"))
    entra(janelas, "_ratio", p_ratio)
    salas = sai(janelas, "hb_objs")
    entra(grade, "_rooms", salas)
    entra(grade, "_grid_size", p_grade)
    entra(grade, "_dist_floor_", p_altura)
    entra(modelo, "rooms_", salas)
    # a grade entra pelo HB Assign Grids and Views (o HB Model nem sempre tem grids_)
    entra(atribuir, "_model", sai(modelo, "model"))
    entra(atribuir, "grids_", sai(grade, "grid", "grids"))
    m = sai(atribuir, "model")
    entra(wea, "_epw_file", p_epw)
    entra(anual, "_model", m)
    entra(anual, "_wea", sai(wea, "wea"))
    entra(anual, "radiance_par_", p_par_anual)
    entra(anual, "_run", rodar)
    entra(df, "_model", m)
    entra(df, "radiance_par_", p_par_df)
    entra(df, "_run", rodar)

    ligar(entrada["modelo_hb"], m)
    ligar(entrada["hb_da"], sai(anual, "DA"))
    ligar(entrada["hb_udi"], sai(anual, "UDI"))
    ligar(entrada["hb_udi_baixo"], sai(anual, "UDI_low"))
    ligar(entrada["hb_udi_alto"], sai(anual, "UDI_up"))
    ligar(entrada["hb_resultados"], sai(anual, "results"))
    ligar(entrada["hb_df"], sai(df, "results"))
    return {"salas": salas, "wea": sai(wea, "wea"), "rodar": rodar, "sai": sai, "entra": entra}


def montar_ofuscamento(gh, doc, hb, entrada):
    """1e: mesma sala, grade de 8 vistas (2 posições × N/L/S/O a 1,2 m) e
    HB Imageless Annual Glare (DGP hora a hora + GA)."""
    import Rhino.Geometry as rg

    if hb is None or hb["salas"] is None:
        print("Aviso: estudo de ofuscamento não montado (falta a sala do HB).")
        return
    sai, entra = hb["sai"], hb["entra"]
    k = Rhino.RhinoMath.UnitScale(Rhino.UnitSystem.Meters, Rhino.RhinoDoc.ActiveDoc.ModelUnitSystem)
    x0, y0 = 150, 2500
    pts = gh.Kernel.Parameters.Param_Point()
    pts.NickName = "olhos"
    vets = gh.Kernel.Parameters.Param_Vector()
    vets.NickName = "direções"
    for (x, y) in [(103.0, 4.0), (103.0, 1.5)]:
        for d in [(0, 1, 0), (1, 0, 0), (0, -1, 0), (-1, 0, 0)]:
            pts.PersistentData.Append(gh.Kernel.Types.GH_Point(rg.Point3d(x * k, y * k, 1.2 * k)))
            vets.PersistentData.Append(gh.Kernel.Types.GH_Vector(rg.Vector3d(*d)))
    colocar(doc, pts, x0, y0)
    colocar(doc, vets, x0, y0 + 50)

    grade = criar_por_nome(gh, doc, "HB Sensor Grid", x0 + 200, y0)
    modelo = criar_por_nome(gh, doc, "HB Model", x0 + 200, y0 + 160)
    atribuir = criar_por_nome(gh, doc, "HB Assign Grids and Views", x0 + 400, y0 + 60)
    glare = criar_por_nome(gh, doc, "HB Imageless Annual Glare", x0 + 650, y0)
    if None in (grade, modelo, atribuir, glare):
        print("Aviso: estudo de ofuscamento incompleto; ligue o que faltar à mão.")
    nome = painel(gh, doc, "vistas", "nome da grade", x0 + 200, y0 - 40, 80)
    p_par = painel(gh, doc, "-ab 5 -ad 20000 -lw 2e-05", "radiance_par ofusc.", x0 + 400, y0 + 160, 220)
    p_lum = painel(gh, doc, "2000", "luminance_factor", x0 + 400, y0 + 200, 60)

    entra(grade, "_name_", nome)
    entra(grade, "_positions", pts)
    entra(grade, "_directions_", vets)
    entra(modelo, "rooms_", hb["salas"])
    entra(atribuir, "_model", sai(modelo, "model"))
    entra(atribuir, "grids_", sai(grade, "grid", "grids"))
    m = sai(atribuir, "model")
    entra(glare, "_model", m)
    entra(glare, "_wea", hb["wea"])
    entra(glare, "_luminance_factor_", p_lum)
    entra(glare, "radiance_par_", p_par)
    entra(glare, "_run", hb["rodar"])

    ligar(entrada["modelo_hb_ofusc"], m)
    ligar(entrada["hb_ga"], sai(glare, "GA"))
    ligar(entrada["hb_ofusc_resultados"], sai(glare, "results"))


def _comp_karamba(gh, doc, nomes, x, y):
    """Componente do Karamba pelo primeiro nome que existir (os nomes variam entre versões)."""
    for px in gh.Instances.ComponentServer.ObjectProxies:
        if px.Desc.Name in nomes:
            obj = gh.Instances.ComponentServer.EmitObject(px.Guid)
            if obj is not None:
                return colocar(doc, obj, x, y)
    print("Aviso: componente do Karamba '" + "/".join(nomes) + "' não encontrado (Karamba3D instalado?).")
    return None


def _param_kr(comp, lado, apelidos, primeiro=False):
    """Parâmetro pelo apelido/nome (sem diferenciar maiúsculas); se não achar e
    primeiro=True, usa o primeiro parâmetro."""
    if comp is None:
        return None
    lista = comp.Params.Input if lado == "in" else comp.Params.Output
    alvos = [a.lower() for a in apelidos]
    for p in lista:
        if (p.NickName or "").lower() in alvos or (p.Name or "").lower() in alvos:
            return p
    if primeiro and lista.Count:
        return lista[0]
    print("Aviso: " + comp.Name + " sem parâmetro " + "/".join(apelidos) + "; ligue à mão.")
    return None


def montar_karamba(gh, doc, entrada):
    """Etapa 2: um modelo do Karamba por caso de testes/referencias/karamba/entrada_<nome>.json.
    Caso Karamba (lê o JSON) -> Line to Beam/Truss, Support, Loads -> Assemble -> Analyze ->
    Nodal Displacements, Beam Forces, Buckling Modes -> Empacotar Karamba -> exportador."""
    pasta_kr = os.path.join(PASTA_REF, "karamba")
    for n, nome in enumerate(CASOS_KARAMBA):
        x0, y0 = 150, 3300 + 700 * n
        arq = painel(gh, doc, os.path.join(pasta_kr, "entrada_" + nome + ".json"), "arquivo " + nome,
                     x0, y0, 360)
        caso = criar_ghpython(gh, doc, x0 + 420, y0, ENTRADAS_CASO_KR, SAIDAS_CASO_KR,
                              CODIGO_CASO_KR, "Caso Karamba " + nome)
        ligar(caso.Params.Input[0], arq)
        sai = dict((p.NickName, p) for p in caso.Params.Output)

        viga = _comp_karamba(gh, doc, ("Line to Beam", "LineToBeam"), x0 + 650, y0)
        trel = _comp_karamba(gh, doc, ("Line to Truss", "LineToTruss"), x0 + 650, y0 + 120)
        apoio = _comp_karamba(gh, doc, ("Support", "Supports"), x0 + 650, y0 + 240)
        carga = _comp_karamba(gh, doc, ("Point-Load", "Point Load", "Loads", "Load"), x0 + 650, y0 + 380)
        mont = _comp_karamba(gh, doc, ("Assemble Model", "Assemble"), x0 + 900, y0 + 100)
        anal = _comp_karamba(gh, doc, ("Analyze", "AnalyzeThI", "Analyze ThI"), x0 + 1100, y0)
        desl = _comp_karamba(gh, doc, ("Nodal Displacements", "NodalDisp"), x0 + 1300, y0)
        forc = _comp_karamba(gh, doc, ("Beam Forces", "Beam Resultant Forces"), x0 + 1300, y0 + 160)
        flam = _comp_karamba(gh, doc, ("Buckling Modes", "BucklingModes"), x0 + 1300, y0 + 320)
        pac = criar_ghpython(gh, doc, x0 + 1550, y0, ENTRADAS_PACOTE_KR, ("texto",), CODIGO_PACOTE_KR,
                             "Empacotar Karamba " + nome)
        pin = dict((p.NickName, p) for p in pac.Params.Input)

        ligar(_param_kr(viga, "in", ("Line", "Lines", "L"), True), sai["vigas"])
        ligar(_param_kr(trel, "in", ("Line", "Lines", "L"), True), sai["trelicas"])
        ligar(_param_kr(apoio, "in", ("Pos|Ind", "Pos", "Pt", "Point", "Points", "Node", "Nodes"), True),
              sai["apoios"])
        ligar(_param_kr(apoio, "in", ("Cond", "Conditions", "Condition")), sai["condicoes"])
        ligar(_param_kr(carga, "in", ("Pos|Ind", "Pos", "Pt", "Point", "Points"), True), sai["pontos_carga"])
        ligar(_param_kr(carga, "in", ("Force", "Vec", "F", "Force-Vector")), sai["forcas"])
        ligar(_param_kr(carga, "in", ("Moment", "Mom", "M", "Moment-Vector")), sai["momentos"])
        el = _param_kr(mont, "in", ("Elem", "Element", "Elements"))
        ligar(el, _param_kr(viga, "out", ("Elem", "Element", "Beam", "Beams"), True))
        ligar(el, _param_kr(trel, "out", ("Elem", "Element", "Truss", "Trusses"), True))
        ligar(_param_kr(mont, "in", ("Point", "Points", "Pt", "Node", "Nodes")), sai["nos"])
        ligar(_param_kr(mont, "in", ("Support", "Supports")), _param_kr(apoio, "out", ("Support",), True))
        ligar(_param_kr(mont, "in", ("Load", "Loads")), _param_kr(carga, "out", ("Load", "Loads"), True))
        modelo = _param_kr(mont, "out", ("Model",), True)
        ligar(_param_kr(anal, "in", ("Model",), True), modelo)
        m_anal = _param_kr(anal, "out", ("Model",), True)
        for c in (desl, forc, flam):
            ligar(_param_kr(c, "in", ("Model",), True), m_anal)

        ligar(pin["nome"], sai["nome"])
        ligar(pin["massa"], _param_kr(mont, "out", ("Mass",)))
        ligar(pin["desloc_max"], _param_kr(anal, "out", ("Disp", "D", "Displacement", "MaxDisp")))
        ligar(pin["energia"], _param_kr(anal, "out", ("Energy", "Comp", "E", "DefEnergy")))
        ligar(pin["trans"], _param_kr(desl, "out", ("Trans", "Translations")))
        ligar(pin["rot"], _param_kr(desl, "out", ("Rot", "Rotations")))
        for k in ("N", "V", "M"):
            ligar(pin[k], _param_kr(forc, "out", (k,)))
        ligar(pin["flambagem"], _param_kr(flam, "out", ("BLFacs", "BLFac", "Factors", "LoadFactors")))
        ligar(pin["modelo"], m_anal)
        ligar(entrada["karamba"], pac.Params.Output[pac.Params.Output.Count - 1])

        nota = painel(
            gh, doc,
            "CONFERIR NO KARAMBA (" + nome + "): Support com as condições do painel 'condicoes' "
            "(pórtico: engaste x y z rx ry rz; treliça: só x y z); Loads do tipo ponto (Point load); "
            "seção e material padrão (RO114.3/4, S235). Se um parâmetro não ligou, veja os avisos "
            "na linha de comando do Rhino e ligue à mão.",
            "LEIA Karamba " + nome, x0, y0 + 60, 380)
        nota.Properties.Multiline = True
        nota.Attributes.Bounds = sd.RectangleF(x0, y0 + 60, 380, 110)


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
    hb = montar_honeybee(gh, doc, p_epw, entrada)
    montar_ofuscamento(gh, doc, hb, entrada)
    montar_karamba(gh, doc, entrada)

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

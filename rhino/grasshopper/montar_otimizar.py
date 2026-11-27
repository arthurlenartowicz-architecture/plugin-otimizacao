# -*- coding: utf-8 -*-
"""Monta no Grasshopper a definição de exemplo do componente Otimizar.

Como usar (uma vez): no Rhino 8, digite na linha de comando

    _-RunPythonScript "C:\\Users\\PC\\Documents\\plugin-otimizacao\\rhino\\grasshopper\\montar_otimizar.py"

Cria um documento novo com:
  - "Sala exemplo" (GhPython, código de sala_exemplo.py): a sala do protótipo em Breps;
  - 5 sliders (janela_N, janela_L, janela_S, janela_O, rotacao_graus) ligados na sala
    e no genoma;
  - "Otimizar" (GhPython, código de otimizar_gh.py), painéis de problema, opções,
    log e métricas, botões iniciar / parar / avaliar;
  - "Pareto" (o mesmo do montar_pareto.py), abaixo, com caso = sala_gh: recarrega
    sozinho no fim da otimização, e o `aplicar` põe a solução nos sliders;
e salva rhino/grasshopper/otimizar.gh. Nada é calculado na montagem. Para usar com a
sua definição: copie o componente Otimizar com os painéis e troque a sala pelos seus
componentes.
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
# montar_otimizar_cs.py liga _USAR_CS: o Otimizar vem do plugin C# (Otimiza.gha), não do GhPython
USAR_CS = globals().get("_USAR_CS", False)
GUID_OTIMIZAR_CS = "c3a1f6d2-8b4e-4a0f-9e57-2d6b1f0a7c93"
SAIDA_GH = os.path.join(PASTA_GH, "otimizar_cs.gh" if USAR_CS else "otimizar.gh")
# quem chama por exec pode trocar o problema, o caso e o arquivo (ex.: conferir_1objetivo.py)
SAIDA_GH = globals().get("_SAIDA_GH") or SAIDA_GH
PROBLEMA = globals().get("_PROBLEMA", "max sol_luz.da\nmax sol_luz.udi\nmin sol_luz.ase")
CASO = globals().get("_CASO", "sala_gh")
OPCOES = globals().get("_OPCOES")          # texto do painel opcoes (None = o padrão abaixo)

LISTA, ITEM = "list", "item"
OTIMIZAR_IN = ([("genoma", LISTA, "sliders e Value Lists que o otimizador mexe")]
               + [(r, LISTA, "geometria: " + r) for r in
                  ("fachada", "vidro", "cobertura", "piso", "interno")]
               + [(r, LISTA, "geometria fixa (mandada uma vez): " + r) for r in ("contexto", "entorno")]
               + [("barras", LISTA, "linhas das barras"), ("apoios", LISTA, "pontos de apoio"),
                  ("cargas", LISTA, "pontos com carga (força em opcoes: forca = 0,0,-10)"),
                  ("secao", LISTA, "seção (ex. RO114.3/4): uma ou uma por barra"),
                  ("problema", LISTA, "uma linha por objetivo/restrição"),
                  ("opcoes", LISTA, "chave = valor"),
                  ("iniciar", ITEM, "botão"), ("parar", ITEM, "botão"), ("avaliar", ITEM, "botão")])
OTIMIZAR_OUT = ["log", "progresso", "metricas", "valores"]
SALA_IN = [(n, ITEM, n) for n in ("janela_N", "janela_L", "janela_S", "janela_O", "rotacao_graus")]
SALA_OUT = ["fachada", "vidro", "cobertura", "piso", "contexto"]


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


def _sem_type_hint(p):
    """Geometria chega como RhinoCommon (não como Guid do ghdoc)."""
    try:
        import GhPython
        p.TypeHint = GhPython.Component.NoChangeHint()
    except Exception:
        pass


def ghpython(gh, doc, arquivo, apelido, entradas, saidas, x, y):
    proxy = None
    for px in gh.Instances.ComponentServer.ObjectProxies:
        if px.Desc.Name == "GhPython Script":
            proxy = px
            break
    if proxy is None:
        raise RuntimeError("Componente 'GhPython Script' não encontrado no Grasshopper.")
    comp = gh.Instances.ComponentServer.EmitObject(proxy.Guid)
    colocar(doc, comp, x, y)
    lado_in, lado_out = gh.Kernel.GH_ParameterSide.Input, gh.Kernel.GH_ParameterSide.Output
    for p in list(comp.Params.Input):
        comp.Params.UnregisterInputParameter(p, True)
    for i, (nome, acesso, desc) in enumerate(entradas):
        p = comp.CreateParameter(lado_in, i)
        p.Name = p.NickName = nome
        p.Description = desc
        p.Optional = True
        p.Access = gh.Kernel.GH_ParamAccess.list if acesso == LISTA else gh.Kernel.GH_ParamAccess.item
        _sem_type_hint(p)
        comp.Params.RegisterInputParam(p, i)
    for p in list(comp.Params.Output):
        if p.NickName != "out":
            comp.Params.UnregisterOutputParameter(p, True)
    for nome in saidas:
        p = comp.CreateParameter(lado_out, comp.Params.Output.Count)
        p.Name = p.NickName = p.Description = nome
        comp.Params.RegisterOutputParam(p, comp.Params.Output.Count)
    comp.Params.OnParametersChanged()
    comp.VariableParameterMaintenance()
    codigo = os.path.join(PASTA_GH, arquivo)
    comp.Code = (
        "# Carrega o código atual de %s (não edite aqui; edite o arquivo)\n"
        "import io\n"
        "_arq = r\"%s\"\n"
        "_src = io.open(_arq, encoding='utf-8').read().replace('# -*- coding: utf-8 -*-', '')\n"
        "exec(compile(_src, _arq, 'exec'))\n" % (arquivo, codigo))
    comp.NickName = apelido
    comp.Attributes.ExpireLayout()
    return comp


def componente_cs(gh, doc, x, y):
    comp = gh.Instances.ComponentServer.EmitObject(System.Guid(GUID_OTIMIZAR_CS))
    if comp is None:
        raise RuntimeError("Componente Otimizar (C#) não encontrado: carregue o Otimiza.gha "
                           "(rhino/plugin/LEIAME.md) e reinicie o Rhino.")
    colocar(doc, comp, x, y)
    comp.NickName = "Otimizar"
    return comp


def slider(gh, doc, nome, minimo, maximo, casas, valor, x, y):
    s = gh.Kernel.Special.GH_NumberSlider()
    s.Slider.Minimum = System.Decimal(minimo)
    s.Slider.Maximum = System.Decimal(maximo)
    s.Slider.DecimalPlaces = casas
    if casas == 0:
        s.Slider.Type = gh.GUI.Base.GH_SliderAccuracy.Integer
    s.Slider.Value = System.Decimal(valor)
    s.NickName = nome
    colocar(doc, s, x, y)
    return s


def botao(gh, doc, nome, x, y):
    b = colocar(doc, gh.Kernel.Special.GH_ButtonObject(), x, y)
    b.NickName = nome
    return b


def montar():
    gh_plugin = Rhino.RhinoApp.GetPlugInObject("Grasshopper")
    if gh_plugin is None:
        raise RuntimeError("Grasshopper não encontrado.")
    gh_plugin.ShowEditor()
    import Grasshopper as gh

    doc = gh.Kernel.GH_Document()
    gh.Instances.DocumentServer.AddDocument(doc)
    gh.Instances.ActiveCanvas.Document = doc

    sala = ghpython(gh, doc, "sala_exemplo.py", "Sala exemplo", SALA_IN, SALA_OUT, 330, 120)
    if USAR_CS:
        otim = componente_cs(gh, doc, 700, 160)
    else:
        otim = ghpython(gh, doc, "otimizar_gh.py", "Otimizar", OTIMIZAR_IN, OTIMIZAR_OUT, 700, 160)
    s_in = dict((p.NickName, p) for p in sala.Params.Input)
    s_out = dict((p.NickName, p) for p in sala.Params.Output)
    o_in = dict((p.NickName, p) for p in otim.Params.Input)
    o_out = dict((p.NickName, p) for p in otim.Params.Output)
    valores = {"janela_N": 0.4, "janela_L": 0.0, "janela_S": 0.0, "janela_O": 0.0}
    for k, nome in enumerate(["janela_N", "janela_L", "janela_S", "janela_O"]):
        s = slider(gh, doc, nome, 0.0, 0.8, 2, valores[nome], 60, 60 + 30 * k)
        s_in[nome].AddSource(s)
        o_in["genoma"].AddSource(s)
    s = slider(gh, doc, "rotacao_graus", -45, 45, 0, 0, 60, 180)
    s_in["rotacao_graus"].AddSource(s)
    o_in["genoma"].AddSource(s)
    for nome in SALA_OUT:
        o_in[nome].AddSource(s_out[nome])
    if PROBLEMA:     # None (conferir_janela.py): o problema vem da janela de Otimização
        o_in["problema"].AddSource(painel(gh, doc, PROBLEMA, "problema", 460, 330, 180, 60, True))
    texto_op = OPCOES if OPCOES is not None else (
        "caso = " + CASO + "\npopulacao = 48\ngeracoes = 20\nnivel = rapido\nlote = 4\n"
        "malha = minima\nmodo = principal")
    o_in["opcoes"].AddSource(painel(gh, doc, texto_op, "opcoes", 460, 400, 180, 100, True))
    for k, nome in enumerate(["iniciar", "parar", "avaliar"]):
        o_in[nome].AddSource(botao(gh, doc, nome, 520, 520 + 30 * k))
    painel(gh, doc, "", "log", 980, 80, 460, 160, True).AddSource(o_out["log"])
    painel(gh, doc, "", "progresso", 980, 250, 200, 20).AddSource(o_out["progresso"])
    painel(gh, doc, "", "valores", 980, 280, 300, 120, True).AddSource(o_out["valores"])
    painel(gh, doc, "", "metricas", 980, 410, 560, 200, True).AddSource(o_out["metricas"])
    painel(gh, doc,
           "Sliders ligados em 'genoma' = parâmetros. Geometria por rótulo. 'avaliar' confere a "
           "variante atual; 'iniciar' otimiza (Esc para). Resultado em resultados/<caso>/, "
           "visto no pareto.gh (botão 'aplicar' devolve a solução aos sliders).",
           "LEIA", 60, 260, 360, 80, True)
    # Pareto na mesma definição (código do montar_pareto.py, sem montar o pareto.gh)
    import io
    arq = os.path.join(PASTA_GH, "montar_pareto.py")
    ns = {"__file__": arq, "__name__": "montar_pareto", "_SO_FUNCOES": True}
    exec(compile(io.open(arq, encoding="utf-8").read().replace("# -*- coding: utf-8 -*-", ""),
                 arq, "exec"), ns)
    ns["adicionar_pareto"](gh, doc, x0=200, y0=700, caso=CASO, com_rodar=False)
    painel(gh, doc, "PARETO: depois de otimizar, escolha a solução no slider 'solucao' e "
                    "aperte 'aplicar' para levá-la aos sliders lá em cima.",
           "LEIA", 60, 820, 360, 50, True)
    gh.Instances.ActiveCanvas.Refresh()
    gh.Kernel.GH_DocumentIO(doc).SaveQuiet(SAIDA_GH)
    print("Definição montada e salva em " + SAIDA_GH)


montar()

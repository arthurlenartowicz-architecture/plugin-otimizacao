#! python 2
# -*- coding: utf-8 -*-
"""Componente "Otimizar": o Grasshopper gera a geometria, o motor em Python avalia na GPU.

Como no Galapagos: ligue em `genoma` os sliders e Value Lists que o otimizador pode
mexer, e a geometria resultante nas entradas por rótulo. Aperte `iniciar`. O laço
(mexer nos sliders → recalcular → ler a geometria) roda num timer da interface, fora
do SolveInstance, em blocos curtos: o Rhino continua respondendo. Esc ou `parar`
interrompem (o que já foi avaliado é gravado). No fim, o resultado.json vai para
resultados/<caso>/ e o componente Pareto mostra a frente (e aplica nos sliders).

ENTRADAS (list access, sem type hint; todas opcionais menos genoma)
  genoma     sliders (contínuo/inteiro: faixa e casas decimais do slider) e Value Lists
             (categórico: nomes dos itens). O nome do parâmetro é o NickName.
  fachada, vidro, cobertura, piso, interno   geometria do projeto (Brep, Mesh, Surface, Extrusion)
  contexto, entorno                          geometria FIXA (mandada uma vez só)
  barras     linhas/curvas das barras (estrutura); apoios: pontos; cargas: pontos
  secao      nome da seção (catálogo TUBOS, ex. "RO114.3/4"), uma ou uma por barra
  problema   Panel, uma linha por objetivo/restrição: "max sol_luz.sda", "sol_luz.ase",
             "sol_luz.da >= 50"
  opcoes     Panel "chave = valor": populacao, geracoes, nivel, algoritmo, semente, epw,
             caso, porta, malha, lote, mostrar, modo, apoio, forca, trelica, gravidade,
             rho_<rótulo>, tau_vidro (ver LEIAME.md)
             modo = trabalhador: este Rhino só ajuda — pega tarefas da otimização que
             outro Rhino (o principal, com a mesma definição) iniciou; fica esperando
             as próximas até `parar`
  iniciar    botão: começa a otimização
  parar      botão: interrompe
  avaliar    botão: avalia só a variante atual (mostra as métricas e grava
             testes/referencias/gh/variante_<caso>.json para a validação)
SAÍDAS
  log, progresso, metricas (todas as métricas disponíveis), valores (de `avaliar`)
"""
import io
import json
import math
import os
import time

import Rhino
import Rhino.Geometry as rg
import scriptcontext as sc
import System
import Grasshopper as gh
from Grasshopper.Kernel.Special import GH_NumberSlider, GH_ValueList

RAIZ_PADRAO = r"C:\Users\PC\Documents\plugin-otimizacao"
try:
    RAIZ = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
except NameError:
    RAIZ = RAIZ_PADRAO
if not os.path.isfile(os.path.join(RAIZ, "CLAUDE.md")):
    RAIZ = RAIZ_PADRAO

VERSAO = 1
ROTULOS_PROJETO = ("fachada", "vidro", "cobertura", "piso", "interno")
ROTULOS_FIXOS = ("contexto", "entorno")
CHAVE = "otimiza_laco"


def _g(nome, padrao=None):
    v = globals().get(nome)
    if v is None or v == "" or v == []:
        return padrao
    return v


def _lista(v):
    if v is None:
        return []
    if isinstance(v, (list, tuple)):
        return [x for x in v if x is not None]
    try:
        return [x for x in list(v) if x is not None]
    except TypeError:
        return [v]


def opcoes_de(texto):
    d = {}
    for linha in _lista(texto):
        for sub in (u"%s" % linha).splitlines():
            sub = sub.split("#")[0]
            if "=" in sub:
                k, v = sub.split("=", 1)
                d[k.strip().lower()] = v.strip()
    return d


def _num(d, k, padrao, tipo=float):
    try:
        return tipo(d[k]) if k in d and d[k] != "" else padrao
    except ValueError:
        return padrao


def _sim(d, k, padrao=False):
    if k not in d:
        return padrao
    return d[k].strip().lower() in ("sim", "s", "true", "1", "yes", "verdadeiro")


# ---------------------------------------------------------------- HTTP (localhost)
class ErroServidor(Exception):
    pass


def http(porta, metodo, rota, dados=None, timeout_ms=600000):
    import System.Net as net
    req = net.WebRequest.Create("http://127.0.0.1:%d%s" % (porta, rota))
    req.Method = metodo
    req.Timeout = timeout_ms
    req.ReadWriteTimeout = timeout_ms
    req.ContentType = "application/json"
    req.Proxy = None
    if dados is not None:
        b = System.Text.Encoding.UTF8.GetBytes(json.dumps(dados))
        req.ContentLength = b.Length
        s = req.GetRequestStream()
        s.Write(b, 0, b.Length)
        s.Close()
    elif metodo == "POST":
        req.ContentLength = 0
    try:
        resp = req.GetResponse()
    except net.WebException as e:
        resp = e.Response
        if resp is None:
            raise ErroServidor(u"servidor fora do ar (%s)" % e.Message)
    leitor = System.IO.StreamReader(resp.GetResponseStream(), System.Text.Encoding.UTF8)
    txt = leitor.ReadToEnd()
    codigo = int(resp.StatusCode)
    resp.Close()
    d = json.loads(txt) if txt else {}
    if codigo >= 400:
        raise ErroServidor(d.get("erro") or txt)
    return d


def servidor_vivo(porta):
    try:
        http(porta, "GET", "/estado", timeout_ms=1500)
        return True
    except Exception:
        return False


def subir_servidor(porta):
    """Abre o servidor (janela de console própria) com o Python da .venv."""
    import System.Diagnostics as sdg
    py = os.path.join(RAIZ, ".venv", "Scripts", "python.exe")
    info = sdg.ProcessStartInfo(py, "-m otimiza.servidor --porta %d" % porta)
    info.WorkingDirectory = RAIZ
    info.UseShellExecute = False
    info.CreateNoWindow = False
    info.EnvironmentVariables["PYTHONPATH"] = os.path.join(RAIZ, "src")
    info.EnvironmentVariables["PYTHONIOENCODING"] = "utf-8"
    sdg.Process.Start(info)


# ---------------------------------------------------------------- genoma
def genoma_de(componente):
    """(parametros do protocolo, objetos do GH) a partir das fontes ligadas em `genoma`."""
    entrada = [p for p in componente.Params.Input if p.NickName == "genoma"]
    if not entrada:
        raise ValueError("componente sem a entrada 'genoma'")
    params, objs, nomes = [], [], set()
    for src in entrada[0].Sources:
        nome = (src.NickName or "").strip() or "p"
        base, k = nome, 2
        while nome in nomes:
            nome = "%s_%d" % (base, k)
            k += 1
        nomes.add(nome)
        if isinstance(src, GH_NumberSlider):
            s = src.Slider
            lo, hi = float(s.Minimum), float(s.Maximum)
            tipo = str(s.Type)
            if tipo == "Integer":
                d = dict(nome=nome, tipo="inteiro", minimo=int(round(lo)), maximo=int(round(hi)))
            elif tipo in ("Even", "Odd"):
                ops = [i for i in range(int(math.ceil(lo)), int(math.floor(hi)) + 1)
                       if i % 2 == (0 if tipo == "Even" else 1)]
                d = dict(nome=nome, tipo="categorico", opcoes=[str(i) for i in ops])
            else:
                d = dict(nome=nome, tipo="continuo", minimo=lo, maximo=hi, casas=int(s.DecimalPlaces))
            d["_valor"] = float(src.CurrentValue)
        elif isinstance(src, GH_ValueList):
            ops = [it.Name for it in src.ListItems]
            sel = src.FirstSelectedItem
            d = dict(nome=nome, tipo="categorico", opcoes=ops,
                     _valor=sel.Name if sel is not None else ops[0])
        else:
            raise ValueError(u"'%s' ligado em genoma não é slider nem Value List" % nome)
        d["_guid"] = str(src.InstanceGuid)
        params.append(d)
        objs.append(src)
    if not params:
        raise ValueError("ligue sliders ou Value Lists em 'genoma'")
    return params, objs


def aplicar_valores(params, objs, valores):
    """Põe os valores nos sliders/listas sem disparar soluções (ExpireSolution(False))."""
    for p, o in zip(params, objs):
        v = valores[p["nome"]]
        if isinstance(o, GH_NumberSlider):
            o.Slider.RaiseEvents = False
            o.SetSliderValue(System.Decimal(float(v)))
            o.Slider.RaiseEvents = True
            o.ExpireSolution(False)
        else:
            nome = u"%s" % v
            for it in o.ListItems:
                it.Selected = (it.Name == nome)
            o.ExpireSolution(False)


# ---------------------------------------------------------------- geometria → protocolo
def _b64(arr, tamanho):
    b = System.Array.CreateInstance(System.Byte, len(arr) * tamanho)
    System.Buffer.BlockCopy(arr, 0, b, 0, b.Length)
    return System.Convert.ToBase64String(b)


def _parametros_malha(qual):
    if qual in ("minima", "", None):
        mp = rg.MeshingParameters.Minimal
        mp.SimplePlanes = True       # face plana = poucos triângulos (não uma grade)
        return mp
    return {"rapida": rg.MeshingParameters.FastRenderMesh, "padrao": rg.MeshingParameters.Default,
            "qualidade": rg.MeshingParameters.QualityRenderMesh}.get(qual) or _parametros_malha("minima")


def _geom(o):
    if isinstance(o, System.Guid):          # type hint "ghdoc Object": Guid no ghdoc
        import rhinoscriptsyntax as rs
        return rs.coercegeometry(o)
    if hasattr(o, "Value") and not isinstance(o, rg.GeometryBase):   # Goo
        return o.Value
    return o


def malha_de(objetos, mp):
    m = rg.Mesh()
    for o in _lista(objetos):
        g = _geom(o)
        if g is None:
            continue
        if isinstance(g, rg.Mesh):
            m.Append(g)
            continue
        if isinstance(g, (rg.Extrusion, rg.Surface)):
            g = g.ToBrep()
        if isinstance(g, rg.Brep):
            for x in rg.Mesh.CreateFromBrep(g, mp) or []:
                m.Append(x)
        else:
            raise ValueError(u"geometria não suportada: %s" % type(g).__name__)
    if m.Faces.Count == 0:
        return None
    m.Faces.ConvertQuadsToTriangles()
    return {"vertices": _b64(m.Vertices.ToFloatArray(), 4), "faces": _b64(m.Faces.ToIntArray(True), 4)}


def _ambiente(o):
    """Objeto do componente Ambiente (Otimiza.gha): (nome, geometria) ou None."""
    g = _geom(o)
    if hasattr(g, "Nome") and hasattr(g, "Geometria"):
        return g.Nome, g.Geometria
    return None


def malhas_de(entradas, rotulos, mp):
    saida = {}
    for r in rotulos:
        objs = _lista(entradas.get(r))
        ambs = [a for a in (_ambiente(o) for o in objs) if a] if r == "piso" else []
        if ambs:   # pisos com nome de ambiente (módulo programa): um item por ambiente
            itens = []
            for nome, g in ambs:
                m = malha_de([g], mp)
                if m is not None:
                    m["ambiente"] = nome
                    itens.append(m)
            m = malha_de([o for o in objs if not _ambiente(o)], mp)
            if m is not None:
                itens.append(m)
            if itens:
                saida[r] = itens
            continue
        m = malha_de(objs, mp)
        if m is not None:
            saida[r] = m
    return saida


def _segmentos(objetos):
    pts = []
    for o in _lista(objetos):
        g = _geom(o)
        if isinstance(g, rg.Line):
            segs = [g]
        elif isinstance(g, rg.Curve):
            ok, pl = g.TryGetPolyline()
            segs = list(pl.GetSegments()) if ok else [rg.Line(g.PointAtStart, g.PointAtEnd)]
        else:
            raise ValueError(u"barra não é linha/curva: %s" % type(g).__name__)
        for s in segs:
            pts += [s.From.X, s.From.Y, s.From.Z, s.To.X, s.To.Y, s.To.Z]
    return pts


def _pontos(objetos):
    saida = []
    for o in _lista(objetos):
        g = _geom(o)
        if isinstance(g, rg.Point):
            g = g.Location
        saida += [g.X, g.Y, g.Z]
    return saida


def estrutura_de(entradas, op):
    barras = _segmentos(entradas.get("barras"))
    if not barras:
        return None
    d = {"barras": barras, "trelica": _sim(op, "trelica", False),
         "gravidade": _num(op, "gravidade", 9.81)}
    sec = [u"%s" % s for s in _lista(entradas.get("secao"))] or [op.get("secao", "RO114.3/4")]
    d["secao"] = sec[0] if len(sec) == 1 else sec
    ap = _pontos(entradas.get("apoios"))
    if ap:
        d["apoios"] = [{"pontos": ap, "condicoes": op.get("apoio", "articulado")}]
    cg = _pontos(entradas.get("cargas"))
    if cg:
        f = [float(x) for x in op.get("forca", "0,0,-10").replace(";", ",").split(",")]
        d["cargas"] = [{"pontos": cg, "forca": f}]
    return d


def geometria_atual(entradas, op):
    """A variante que está no canvas agora, no formato do protocolo."""
    e = estrutura_de(entradas, op)
    if e is not None:
        return {"estrutura": e}
    m = malhas_de(entradas, ROTULOS_PROJETO, _parametros_malha(op.get("malha", "minima")))
    if not m:
        raise ValueError("nenhuma geometria nas entradas fachada/vidro/cobertura/piso/interno/barras")
    return {"malhas": m}


def materiais_de(op):
    mat = {}
    for k, v in op.items():
        if k.startswith("rho_"):
            mat.setdefault(k[4:], {})["rho"] = float(v)
    if "tau_vidro" in op:
        mat.setdefault("vidro", {})["tau"] = float(op["tau_vidro"])
    return mat


def escala_modelo():
    doc = Rhino.RhinoDoc.ActiveDoc
    return Rhino.RhinoMath.UnitScale(doc.ModelUnitSystem, Rhino.UnitSystem.Meters)


def pedido_base(op, problema):
    d = dict(versao=VERSAO, problema=[u"%s" % x for x in _lista(problema)],
             nivel=op.get("nivel", "rapido"), escala=escala_modelo(), materiais=materiais_de(op))
    if op.get("epw"):
        d["epw"] = op["epw"]
    if op.get("fatores"):   # tabela de carbono/custo editada pelo usuario (copia do fatores.json)
        d["carbono_custo"] = {"arquivo": op["fatores"]}
    prog = programa_de(op, _g("programa"))
    if prog:
        d["programa"] = prog
    return d


def programa_de(op, textos):
    """Programa (módulo programa): `necessidades = arquivo.json` no painel opcoes, depois os
    textos JSON dos componentes Lote/Programa ligados numa entrada `programa` (se existir)."""
    d = {}
    if op.get("necessidades"):
        d["arquivo"] = op["necessidades"]
    for t in _lista(textos):
        t = (u"%s" % t).strip()
        if not t:
            continue
        x = json.loads(t)
        adj = dict(d.get("adjacencia") or {}, **(x.get("adjacencia") or {}))
        d.update(x)
        if adj:
            d["adjacencia"] = adj
    return d


# ---------------------------------------------------------------- laço (fora do SolveInstance)
class Laco(object):
    """Pega tarefas do servidor, aplica nos sliders, recalcula e devolve a geometria.

    pedido = None → modo trabalhador: não inicia nada; espera uma otimização no servidor
    (aberta por outro Rhino, o principal), pega tarefas da mesma fila e, quando ela acaba,
    espera a próxima, até `parar`."""

    def __init__(self, componente, op, pedido, params, objs):
        self.trabalhador = pedido is None
        self.rodada = None
        self.visto = None
        self.comp = componente
        self.doc = componente.OnPingDocument()
        self.op = op
        self.porta = _num(op, "porta", 8765, int)
        self.lote = _num(op, "lote", 4, int)   # pequeno: divide melhor entre vários Rhinos
        self.mostrar = _sim(op, "mostrar", False)
        self.pedido = pedido
        self.params, self.objs = params, objs
        self.originais = dict((p["nome"], p["_valor"]) for p in params)
        self.estado = "conectando"
        self.capturando = False
        self.captura = None
        self.fila, self.prontos = [], []
        self.log = []
        self.ultimo = {}
        self.t_espera = 0.0
        self.t_inicio = time.time()
        self.tempos = []
        self.ocupado = False
        self.medidor = False
        import Eto.Forms as ef
        self.timer = ef.UITimer()
        self.timer.Interval = 0.005
        self.timer.Elapsed += self.tick
        if self.trabalhador:
            self.escrever(u"Modo trabalhador: esperando uma otimização no servidor (porta %d)."
                          % self.porta)
        elif not servidor_vivo(self.porta):
            subir_servidor(self.porta)
            self.escrever(u"Subindo o servidor (1ª vez carrega a GPU, ~10-20 s)…")
        self.timer.Start()

    def _ver(self, desc):
        """Diagnóstico do trabalhador: registra quando muda o que ele vê no servidor."""
        if desc != self.visto:
            self.visto = desc
            if self.trabalhador:
                self.escrever(u"Trabalhador vê: " + desc)

    def _vista(self, ligada):
        if not self.mostrar:
            Rhino.RhinoDoc.ActiveDoc.Views.RedrawEnabled = ligada

    def escrever(self, txt):
        self.log.append(txt)
        self.log = self.log[-30:]
        Rhino.RhinoApp.WriteLine(u"[Otimizar] " + txt)
        if not self.capturando and self.doc is not None:   # atualiza o painel de log
            comp = self.comp
            self.doc.ScheduleSolution(
                50, gh.Kernel.GH_Document.GH_ScheduleDelegate(lambda d: comp.ExpireSolution(False)))

    # -- interface
    def mensagem(self, txt):
        self.comp.Message = txt
        self.comp.OnDisplayExpired(True)

    def progresso(self):
        u = self.ultimo
        pre = u"trabalhador · " if self.trabalhador else u""
        if self.estado == "conectando" or not u:
            if self.trabalhador:   # contador: mostra que o timer está rodando
                return pre + u"%s · %d s" % (self.visto or u"esperando",
                                             int(time.time() - self.t_inicio))
            return self.estado
        g, G = u.get("geracao", 0), u.get("geracoes", 1)
        txt = pre + u"ger %d/%d" % (min(g + 1, G), G)
        if u.get("estado") == "avaliando":
            txt += u" · GPU"
        elif u.get("lote"):
            txt += u" · %d/%d" % (u["lote"] - u.get("faltam", 0), u["lote"])
        return txt

    def _medidor(self):
        u = self.ultimo
        G, lote = max(u.get("geracoes", 1), 1), max(u.get("lote", 1) or 1, 1)
        feito = u.get("geracao", 0) + (lote - u.get("faltam", lote)) / float(lote)
        pos = int(100 * min(feito / G, 1.0))
        if not self.medidor:
            Rhino.UI.StatusBar.ShowProgressMeter(
                0, 100, "Trabalhando (parar)" if self.trabalhador else "Otimizando (Esc para parar)",
                True, True)
            self.medidor = True
        Rhino.UI.StatusBar.UpdateProgressMeter(pos, True)

    # -- timer
    def tick(self, sender, e):
        if self.ocupado or self.doc is None:
            return
        if self.doc.SolutionState == gh.Kernel.GH_ProcessStep.Process:
            return
        self.ocupado = True
        try:
            if gh.Kernel.GH_Document.IsEscapeKeyDown():
                self.parar(u"Esc")
                return
            if self.estado == "conectando":
                self._conectar()
            elif self.estado == "rodando":
                self._trabalhar()
        except Exception as ex:
            import traceback
            self.escrever(u"ERRO: %s\n%s" % (ex, traceback.format_exc()[-1500:]))
            self.finalizar(u"erro")
        finally:
            self.ocupado = False

    def _conectar(self):
        if not self.trabalhador and time.time() - self.t_inicio > 120:
            raise ErroServidor(u"o servidor não respondeu em 120 s (veja a janela de console)")
        if time.time() < self.t_espera:
            self.mensagem(self.progresso())
            return
        if not servidor_vivo(self.porta):
            self.t_espera = time.time() + (1.0 if self.trabalhador else 0.5)
            self._ver(u"servidor fora do ar")
            self.mensagem(self.progresso())
            return
        if self.trabalhador:
            r = http(self.porta, "GET", "/estado")
            self._ver(u"servidor: %s" % r.get("estado"))
            if r.get("estado") not in ("gerando", "avaliando"):
                self.t_espera = time.time() + 1.0      # nenhuma otimização em andamento
                self.mensagem(self.progresso())
                return
            meus = set(p["nome"] for p in self.params)
            faltam = [n for n in r.get("parametros", []) if n not in meus]
            if faltam:
                raise ValueError(u"a otimização usa parâmetros que este Rhino não tem no genoma: "
                                 + u", ".join(faltam) + u" (abra a mesma definição)")
            self.rodada = r.get("rodada")
            self.escrever(u"Entrou na otimização (rodada %s, geração %s)." % (
                self.rodada, r.get("geracao")))
        else:
            r = http(self.porta, "POST", "/iniciar", self.pedido)
            self.rodada = r.get("rodada")
            self.escrever(u"Otimização iniciada: %s, %s × %s." % (
                r.get("algoritmo"), self.pedido.get("populacao"), self.pedido.get("geracoes")))
        self.ultimo = r
        self.estado = "rodando"
        self._vista(False)

    def _trabalhar(self):
        if time.time() < self.t_espera:
            return
        t_fim = time.time() + 0.25           # bloco curto: depois devolve a vez à interface
        if not self.fila:
            try:
                r = http(self.porta, "POST", "/tarefas", {"maximo": self.lote})
            except ErroServidor:
                if not self.trabalhador:
                    raise
                return self._esperar_proxima(u"servidor sem otimização")
            self.ultimo = r
            if r["estado"] in ("fim", "erro"):
                return self._terminou(r)
            self.fila = list(r.get("tarefas") or [])
            if not self.fila:                 # GPU avaliando: espera sem ocupar a interface
                self.t_espera = time.time() + 0.02   # GPU leva ~65 ms/geração; consulta ~1 ms
                self.mensagem(self.progresso())
                return
        while self.fila and time.time() < t_fim:
            t = self.fila.pop(0)
            self.prontos.append(self._gerar(t))
        if self.prontos and (not self.fila or time.time() >= t_fim):
            r = http(self.porta, "POST", "/resultados", {"resultados": self.prontos})
            self.prontos = []
            self.ultimo = r
            if r.get("erros"):
                self.escrever(u"Variantes com erro (viram inviáveis): " + u"; ".join(r["erros"][:3]))
            if r["estado"] in ("fim", "erro"):
                return self._terminou(r)
        self.mensagem(self.progresso())
        self._medidor()

    def _gerar(self, tarefa):
        t0 = time.time()
        self.captura, self.capturando = None, True
        try:
            aplicar_valores(self.params, self.objs, tarefa["parametros"])
            self.doc.NewSolution(False)
        finally:
            self.capturando = False
        cap = self.captura or {}
        dt = time.time() - t0
        self.tempos.append(dt)
        if cap.get("erro") or not cap.get("geometria"):
            return {"id": tarefa["id"], "erro": cap.get("erro") or u"componente não recalculou",
                    "tempo_s": dt}
        return {"id": tarefa["id"], "geometria": cap["geometria"], "tempo_s": dt}

    # -- fim
    def _terminou(self, r):
        if r.get("estado") == "erro":
            self.escrever(u"ERRO no servidor:\n%s" % r.get("erro"))
        if self.trabalhador:
            return self._esperar_proxima(u"otimização terminou")
        self.finalizar(r.get("estado", "fim"))

    def _esperar_proxima(self, motivo):
        """Trabalhador: volta a esperar uma nova otimização (não para)."""
        self.fila, self.prontos = [], []
        self.estado = "conectando"
        self.t_espera = time.time() + 1.0
        self._vista(True)
        if self.medidor:
            Rhino.UI.StatusBar.HideProgressMeter()
            self.medidor = False
        if self.tempos:
            self.escrever(u"%s (rodada %s). Tempo no GH: %.1f ms/variante (%d variantes). "
                          u"Esperando a próxima." % (motivo, self.rodada,
                                                     1000 * sum(self.tempos) / len(self.tempos),
                                                     len(self.tempos)))
        self.tempos = []
        self.mensagem(self.progresso())

    def parar(self, motivo=u"parado"):
        if self.trabalhador:
            try:   # entrega o que já gerou e devolve o resto para os outros Rhinos
                if self.prontos:
                    http(self.porta, "POST", "/resultados", {"resultados": self.prontos})
                if self.fila:
                    http(self.porta, "POST", "/devolver", {"ids": [t["id"] for t in self.fila]})
            except Exception as ex:
                self.escrever(u"não consegui devolver as tarefas: %s" % ex)
            self.fila, self.prontos = [], []
            self.escrever(u"Trabalhador parado (%s); a otimização segue nos outros Rhinos." % motivo)
            self.finalizar(u"parado")
            return
        if self.estado in ("rodando", "conectando"):
            try:
                self.ultimo = http(self.porta, "POST", "/parar", {})
            except Exception as ex:
                self.escrever(u"não consegui avisar o servidor: %s" % ex)
            self.escrever(u"Interrompido (%s); o que já foi avaliado foi gravado." % motivo)
        self.finalizar(u"parado")

    def finalizar(self, estado):
        self.timer.Stop()
        self.estado = estado
        Rhino.RhinoDoc.ActiveDoc.Views.RedrawEnabled = True
        if self.medidor:
            Rhino.UI.StatusBar.HideProgressMeter()
            self.medidor = False
        u = self.ultimo or {}
        if u.get("arquivo"):
            self.escrever(u"Resultado: %s" % u["arquivo"])
        if self.tempos:
            self.escrever(u"Tempo no GH: %.1f ms/variante (%d variantes); GPU: %s ms/variante."
                          % (1000 * sum(self.tempos) / len(self.tempos), len(self.tempos),
                             u"%.2f" % (1000 * u["tempo_gpu_s_por_variante"])
                             if u.get("tempo_gpu_s_por_variante") else u"?"))
        originais, params, objs, doc = self.originais, self.params, self.objs, self.doc
        comp = self.comp

        def restaurar(d):
            aplicar_valores(params, objs, originais)
            for o in d.Objects:                 # recarrega o componente Pareto, se houver
                if getattr(o, "NickName", "") == "Pareto":
                    o.ExpireSolution(False)
            comp.ExpireSolution(False)
        doc.ScheduleSolution(10, gh.Kernel.GH_Document.GH_ScheduleDelegate(restaurar))


# ---------------------------------------------------------------- SolveInstance
def entradas_atuais():
    return dict((r, _g(r)) for r in ROTULOS_PROJETO + ROTULOS_FIXOS + ("barras", "apoios", "cargas", "secao"))


def executar():
    comp = ghenv.Component  # noqa: F821  (variável do GhPython)
    laco = sc.sticky.get(CHAVE)
    op = opcoes_de(_g("opcoes"))
    entradas = entradas_atuais()

    # 1) chamado pelo próprio laço: só captura a geometria desta variante
    if laco is not None and laco.capturando:
        try:
            laco.captura = {"geometria": geometria_atual(entradas, laco.op)}
        except Exception as ex:
            laco.captura = {"erro": u"%s" % ex}
        return dict(log=u"\n".join(laco.log), progresso=laco.progresso())

    rodando = laco is not None and laco.estado in ("conectando", "rodando")
    if _g("parar", False) and rodando:
        laco.parar(u"botão parar")
        return dict(log=u"\n".join(laco.log), progresso=laco.estado)
    porta = _num(op, "porta", 8765, int)
    saida = {}
    if _g("iniciar", False) and not rodando and op.get("modo", "").lower() == "trabalhador":
        params, objs = genoma_de(comp)        # mesmos sliders do Rhino principal
        laco = Laco(comp, op, None, params, objs)
        sc.sticky[CHAVE] = laco
    elif _g("iniciar", False) and not rodando:
        params, objs = genoma_de(comp)
        problema = _lista(_g("problema"))
        if not problema:
            raise ValueError(u"escreva o problema (Panel ligado em 'problema')")
        caso = op.get("caso", "gh")
        pedido = pedido_base(op, problema)
        pedido.update(parametros=[dict((k, v) for k, v in p.items() if not k.startswith("_"))
                                  for p in params],
                      algoritmo=op.get("algoritmo", "auto"),
                      populacao=_num(op, "populacao", 48, int),
                      geracoes=_num(op, "geracoes", 20, int),
                      semente=_num(op, "semente", 0, int), caso=caso,
                      saida=os.path.join(RAIZ, "resultados", caso),
                      cliente={"genoma": [dict(nome=p["nome"], guid=p["_guid"], tipo=p["tipo"])
                                          for p in params],
                               "arquivo_gh": comp.OnPingDocument().FilePath})
        fixas = malhas_de(entradas, ROTULOS_FIXOS, _parametros_malha(op.get("malha", "minima")))
        if fixas:
            pedido["fixas"] = {"malhas": fixas}
        laco = Laco(comp, op, pedido, params, objs)
        sc.sticky[CHAVE] = laco
    if _g("avaliar", False):
        saida.update(avaliar_atual(comp, op, entradas, porta))
    if laco is not None:
        saida.setdefault("log", u"\n".join(laco.log))
        saida["progresso"] = laco.progresso() if laco.estado == "rodando" else laco.estado
    else:
        saida.setdefault("log", u"Ligue os sliders em 'genoma', a geometria nas entradas por "
                                u"rótulo, escreva o problema e aperte 'iniciar'.")
    if servidor_vivo(porta):
        cache = sc.sticky.get("otimiza_metricas")
        if cache is None:
            m = http(porta, "GET", "/metricas")["metricas"]
            cache = [u"%s.%s  [%s, %s]  %s" % (mod, k, x["unidade"], x["sentido"], x["descricao"])
                     for mod, ms in sorted(m.items()) for k, x in sorted(ms.items())]
            sc.sticky["otimiza_metricas"] = cache
        saida["metricas"] = cache
    return saida


def avaliar_atual(comp, op, entradas, porta):
    """Avalia a variante do canvas (sem otimizar) e grava a referência da validação."""
    if not servidor_vivo(porta):
        subir_servidor(porta)
        return dict(log=u"Subindo o servidor… aperte 'avaliar' de novo em ~20 s.")
    t0 = time.time()
    geo = geometria_atual(entradas, op)
    t_gh = time.time() - t0
    pedido = pedido_base(op, _g("problema") or [])
    pedido["geometrias"] = [geo]
    fixas = malhas_de(entradas, ROTULOS_FIXOS, _parametros_malha(op.get("malha", "minima")))
    if fixas:
        pedido["fixas"] = {"malhas": fixas}
    r = http(porta, "POST", "/avaliar", pedido)
    valores = [u"%s = %s" % (k, v[0]) for k, v in sorted(r["valores"].items())]
    caso = op.get("caso", "gh")
    try:
        params, _ = genoma_de(comp)
        genoma = dict((p["nome"], p["_valor"]) for p in params)
    except Exception:
        genoma = {}
    pasta = os.path.join(RAIZ, "testes", "referencias", "gh")
    if not os.path.isdir(pasta):
        os.makedirs(pasta)
    arq = os.path.join(pasta, "variante_%s.json" % caso)
    ref = dict(pedido, genoma=genoma, valores_gh=r["valores"], tempo_gh_s=t_gh)
    with io.open(arq, "w", encoding="utf-8") as f:
        f.write(u"%s" % json.dumps(ref))       # ensure_ascii: sem problema de acento
    return dict(valores=valores,
                log=u"Avaliado em %.2f s (GH %.0f ms para malhar e converter). Gravado %s"
                    % (r.get("tempo_s", 0), 1000 * t_gh, arq))


try:
    _saida = executar()
except Exception as _ex:
    import traceback
    _saida = dict(log=u"ERRO:\n" + traceback.format_exc())
log = _saida.get("log")
progresso = _saida.get("progresso")
metricas = _saida.get("metricas")
valores = _saida.get("valores")

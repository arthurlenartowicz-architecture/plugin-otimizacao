#! python 2
# -*- coding: utf-8 -*-
"""Etapa 3 (MVP): mostra no Rhino as soluções da frente de Pareto.

Lê o resultado.json gravado por `otimiza.otimizacao.pareto.exportar` (ex.: pelo
scripts/otimizar_exemplo.py) e devolve a geometria e os parâmetros da solução
escolhida. O cálculo (GPU, torch) fica fora do Rhino; o botão `rodar` só chama
o Python da .venv e espera terminar.

ENTRADAS (todas opcionais)
  arquivo            caminho do resultado.json (padrão: resultados/<caso>/resultado.json)
  caso               nome do exemplo (trelica, sala) — usado por `rodar` e pelo padrão acima
  rodar              botão: roda scripts/otimizar_exemplo.py <caso> (trava o GH até acabar)
  solucao            índice da solução (na lista filtrada); passa do fim → volta ao início
  so_representantes  True = só a solução mais central de cada cluster
  espacamento        > 0: `todas` sai enfileirada em x com esse passo (m); 0 = automático
SAÍDAS
  log, parametros ("nome = valor"), valores (só os valores, para ligar em sliders),
  objetivos ("nome = valor"), geometria (solução escolhida: malhas ou linhas),
  vidros (malha só dos vidros), todas (todas as soluções filtradas, lado a lado),
  tabela (uma linha por solução filtrada)
"""
import io
import json
import os

import Rhino.Geometry as rg

RAIZ_PADRAO = r"C:\Users\PC\Documents\plugin-otimizacao"
try:
    RAIZ = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
except NameError:
    RAIZ = RAIZ_PADRAO
if not os.path.isfile(os.path.join(RAIZ, "CLAUDE.md")):
    RAIZ = RAIZ_PADRAO


def _g(nome, padrao=None):
    v = globals().get(nome)
    return padrao if v is None or v == "" else v


def rodar_python(caso):
    """Roda o exemplo com o Python da .venv e devolve a saída de texto."""
    import System
    import System.Diagnostics as sdg
    py = os.path.join(RAIZ, ".venv", "Scripts", "python.exe")
    script = os.path.join(RAIZ, "scripts", "otimizar_exemplo.py")
    info = sdg.ProcessStartInfo(py, '"%s" %s' % (script, caso))
    info.WorkingDirectory = RAIZ
    info.UseShellExecute = False
    info.CreateNoWindow = True
    info.RedirectStandardOutput = True
    info.RedirectStandardError = True
    info.StandardOutputEncoding = System.Text.Encoding.UTF8
    info.StandardErrorEncoding = System.Text.Encoding.UTF8
    p = sdg.Process.Start(info)
    saida = p.StandardOutput.ReadToEnd()
    erro = p.StandardError.ReadToEnd()
    p.WaitForExit()
    if p.ExitCode != 0:
        return u"ERRO ao otimizar (código %d):\n%s\n%s" % (p.ExitCode, saida, erro[-3000:])
    return saida


def _malha(vertices, faces, filtro=None):
    m = rg.Mesh()
    usados = {}
    for f in faces:
        if filtro is not None and not filtro(f):
            continue
        ids = []
        for k in f[:3]:
            if k not in usados:
                x, y, z = vertices[k]
                usados[k] = m.Vertices.Add(x, y, z)
            ids.append(usados[k])
        m.Faces.AddFace(ids[0], ids[1], ids[2])
    m.Normals.ComputeNormals()
    m.Compact()
    return m


def _geometria_de(sol, rotulos, desloc=None):
    """(lista de geometria, malha dos vidros ou None) de uma solução."""
    g = sol.get("geometria")
    if not g:
        return [], None
    mover = rg.Transform.Translation(desloc) if desloc is not None else None
    saida, vidros = [], None
    if g["tipo"] == "barras":
        nos = g["nos"]
        for i, j in g["barras"]:
            c = rg.LineCurve(rg.Point3d(*nos[i]), rg.Point3d(*nos[j]))
            if mover:
                c.Transform(mover)
            saida.append(c)
        return saida, None
    rot = g.get("rotulos") or [0] * len(g["faces"])
    vidro = rotulos.index("vidro") if "vidro" in rotulos else -1
    faces = [f + [r] for f, r in zip(g["faces"], rot)]
    opaca = _malha(g["vertices"], faces, lambda f: f[3] != vidro)
    if vidro >= 0 and any(f[3] == vidro for f in faces):
        vidros = _malha(g["vertices"], faces, lambda f: f[3] == vidro)
    for m in (opaca, vidros):
        if m is not None and mover:
            m.Transform(mover)
    saida.append(opaca)
    if vidros is not None:
        saida.append(vidros)
    return saida, vidros


def _largura(sol):
    g = sol.get("geometria") or {}
    pts = g.get("vertices") or g.get("nos") or [[0, 0, 0]]
    xs = [p[0] for p in pts]
    return max(xs) - min(xs)


def _fmt(v):
    return ("%.4g" % v) if isinstance(v, float) else u"%s" % v


def executar():
    caso = _g("caso", "trelica")
    linhas = []
    if _g("rodar", False):
        linhas.append(rodar_python(caso))
    arq = _g("arquivo") or os.path.join(RAIZ, "resultados", caso, "resultado.json")
    if not os.path.isfile(arq):
        return dict(log=u"\n".join(linhas + [u"Não achei %s. Aperte 'rodar' ou rode "
                                             u"scripts/otimizar_exemplo.py %s." % (arq, caso)]))
    with io.open(arq, encoding="utf-8") as f:
        dados = json.load(f)
    rotulos = dados.get("rotulos", ["fachada", "vidro", "cobertura", "piso", "interno",
                                    "contexto", "entorno"])
    sols = dados["solucoes"]
    if _g("so_representantes", False):
        sols = [s for s in sols if s.get("representante")]
    if not sols:
        return dict(log=u"Nenhuma solução viável no arquivo.")
    nomes_obj = [o["nome"] for o in dados["objetivos"]]
    i = int(_g("solucao", 0)) % len(sols)
    s = sols[i]
    geo, vid = _geometria_de(s, rotulos)
    passo = float(_g("espacamento", 0) or 0) or 1.5 * max(_largura(x) for x in sols) or 10.0
    todas = []
    for k, x in enumerate(sols):
        todas += _geometria_de(x, rotulos, rg.Vector3d(k * passo, 0, 0))[0]
    nomes_par = [p["nome"] for p in dados["parametros"]]
    tabela = [u"#  cluster  " + u"  ".join(nomes_obj + nomes_par)]
    for k, x in enumerate(sols):
        tabela.append(u"%d  %d%s  " % (k, x["cluster"], "*" if x.get("representante") else "")
                      + u"  ".join(_fmt(x["objetivos"][n]) for n in nomes_obj) + u"  "
                      + u"  ".join(_fmt(x["parametros"][n]) for n in nomes_par))
    linhas.append(u"%s: %s, %d avaliações (%d viáveis), %d soluções na lista."
                  % (os.path.basename(os.path.dirname(arq)), dados["algoritmo"],
                     dados["n_avaliacoes"], dados["n_viaveis"], len(sols)))
    linhas.append(u"Solução %d (cluster %d%s)." % (i, s["cluster"],
                                                    ", representante" if s.get("representante") else ""))
    return dict(log=u"\n".join(linhas),
                parametros=[u"%s = %s" % (n, _fmt(s["parametros"][n])) for n in nomes_par],
                valores=[s["parametros"][n] for n in nomes_par],
                objetivos=[u"%s = %s" % (n, _fmt(s["objetivos"][n])) for n in nomes_obj],
                geometria=geo, vidros=vid, todas=todas, tabela=tabela)


try:
    _saida = executar()
except Exception:
    import traceback
    _saida = dict(log=u"ERRO:\n" + traceback.format_exc())
log = _saida.get("log")
parametros = _saida.get("parametros")
valores = _saida.get("valores")
objetivos = _saida.get("objetivos")
geometria = _saida.get("geometria")
vidros = _saida.get("vidros")
todas = _saida.get("todas")
tabela = _saida.get("tabela")

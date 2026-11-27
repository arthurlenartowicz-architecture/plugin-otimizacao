# -*- coding: utf-8 -*-
"""Conferência da 4f2 no Rhino: janela de Otimização (Otimiza.gha).

No Rhino 8, com o Otimiza.gha novo carregado (recompile com o Rhino fechado:
rhino/plugin/LEIAME.md), digite:

    _-RunPythonScript "C:\\Users\\PC\\Documents\\plugin-otimizacao\\rhino\\grasshopper\\conferir_janela.py"

O script fecha o servidor aberto e monta a sala de exemplo com o Otimizar C# SEM o painel
'problema' (quem manda é a janela) e com um painel 'opcoes' só com caso/lote/malha
(população, gerações e nível também vêm da janela). Salva em
resultados/sala_janela/conferir_janela.gh (fora do git).

Roteiro no Grasshopper:
  1. duplo clique no componente Otimizar: abre a janela (os gráficos à direita);
  2. 'Ligar servidor' (ou espere o Iniciar subir); a lista de métricas aparece;
  3. marque sol_luz.da (max) e sol_luz.ase (min); população 48, gerações 20, rápido;
  4. 'Iniciar': a convergência (hipervolume) e a frente aparecem a cada geração;
  5. no fim, clique num ponto da frente e em 'Aplicar no Grasshopper': os sliders mudam e
     a sala aparece no Rhino com essa solução;
  6. salve o .gh, feche e abra de novo: a janela volta com os mesmos objetivos.
Teste também 3 objetivos (marque sol_luz.udi): Pareto 3D e coordenadas paralelas.
"""
import io
import os
import time

RAIZ_PADRAO = r"C:\Users\PC\Documents\plugin-otimizacao"
try:
    _pasta = os.path.dirname(os.path.abspath(__file__))
except NameError:
    _pasta = os.path.join(RAIZ_PADRAO, "rhino", "grasshopper")
_raiz = os.path.abspath(os.path.join(_pasta, "..", ".."))
if not os.path.isfile(os.path.join(_raiz, "CLAUDE.md")):
    _raiz = RAIZ_PADRAO
CASO = "sala_janela"
SAIDA = os.path.join(_raiz, "resultados", CASO, "conferir_janela.gh")


def fechar_servidor(porta=8765):
    url = "http://127.0.0.1:%d/encerrar" % porta
    try:
        try:
            from urllib.request import Request, urlopen      # Python 3 (Rhino 8)
        except ImportError:
            from urllib2 import Request, urlopen             # IronPython
        urlopen(Request(url, data=b"{}", headers={"Content-Type": "application/json"}),
                timeout=3).read()
        print("Servidor antigo fechado.")
        time.sleep(1.0)
    except Exception:
        print("Nenhum servidor aberto (ok).")


fechar_servidor()
if not os.path.isdir(os.path.dirname(SAIDA)):
    os.makedirs(os.path.dirname(SAIDA))
_arq = os.path.join(_pasta, "montar_otimizar.py")
_ns = {"__file__": _arq, "__name__": "__main__", "_USAR_CS": True,
       "_PROBLEMA": None, "_CASO": CASO, "_SAIDA_GH": SAIDA,
       "_OPCOES": "caso = " + CASO + "\nlote = 4\nmalha = minima\nmodo = principal"}
exec(compile(io.open(_arq, encoding="utf-8").read().replace("# -*- coding: utf-8 -*-", ""),
             _arq, "exec"), _ns)
print("Agora dê duplo clique no componente Otimizar para abrir a janela.")

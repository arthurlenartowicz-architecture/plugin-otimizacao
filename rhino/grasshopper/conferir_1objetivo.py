# -*- coding: utf-8 -*-
"""Conferência da 4f1 no Rhino: otimização com 1 objetivo (GA).

No Rhino 8, com o Otimiza.gha carregado (rhino/plugin/LEIAME.md), digite:

    _-RunPythonScript "C:\\Users\\PC\\Documents\\plugin-otimizacao\\rhino\\grasshopper\\conferir_1objetivo.py"

O script:
  1. fecha o servidor que estiver aberto (para o próximo subir com o código novo);
  2. monta a mesma definição do otimizar_cs.gh com o problema "max sol_luz.da" e
     caso = sala_1obj (não mexe no gabarito sala_gh dos testes);
  3. salva em resultados/sala_1obj/conferir_1objetivo.gh (fora do git).

Depois, no Grasshopper:
  - aperte 'iniciar' (o servidor sobe sozinho; a 1ª vez leva ~20 s);
  - no log, o algoritmo deve ser 'ga';
  - no fim, o Pareto embaixo mostra os 20 melhores projetos (solucao 0 = maior DA);
    'aplicar' leva a solução aos sliders.
Para conferir os números: .venv\\Scripts\\python.exe scripts\\conferir_1objetivo.py
Não aperte 'avaliar' nesta definição (gravaria testes/referencias/gh/variante_sala_1obj.json).
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
CASO = "sala_1obj"
SAIDA = os.path.join(_raiz, "resultados", CASO, "conferir_1objetivo.gh")


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
       "_PROBLEMA": "max sol_luz.da", "_CASO": CASO, "_SAIDA_GH": SAIDA}
exec(compile(io.open(_arq, encoding="utf-8").read().replace("# -*- coding: utf-8 -*-", ""),
             _arq, "exec"), _ns)
print("Agora aperte 'iniciar' no componente Otimizar.")

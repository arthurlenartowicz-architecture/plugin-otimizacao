# -*- coding: utf-8 -*-
"""Monta otimizar_cs.gh: a mesma definição do montar_otimizar.py, com o componente Otimizar
do plugin C# (Otimiza.gha) no lugar do GhPython. Precisa do Otimiza.gha carregado
(rhino/plugin/LEIAME.md). No Rhino 8:

    _-RunPythonScript "C:\\Users\\PC\\Documents\\plugin-otimizacao\\rhino\\grasshopper\\montar_otimizar_cs.py"
"""
import io
import os

RAIZ_PADRAO = r"C:\Users\PC\Documents\plugin-otimizacao"
try:
    _pasta = os.path.dirname(os.path.abspath(__file__))
except NameError:
    _pasta = os.path.join(RAIZ_PADRAO, "rhino", "grasshopper")
_arq = os.path.join(_pasta, "montar_otimizar.py")
_ns = {"__file__": _arq, "__name__": "__main__", "_USAR_CS": True}
exec(compile(io.open(_arq, encoding="utf-8").read().replace("# -*- coding: utf-8 -*-", ""),
             _arq, "exec"), _ns)

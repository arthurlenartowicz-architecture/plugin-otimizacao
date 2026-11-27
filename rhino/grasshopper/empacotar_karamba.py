# -*- coding: utf-8 -*-
"""Junta os resultados do Karamba de um caso num texto JSON para o exportador.

ENTRADAS (todas em Tree Access; as que não estiverem ligadas ficam de fora):
  nome        nome do caso (do "Caso Karamba")
  massa       Mass do Assemble Model
  desloc_max  Disp do Analyze
  energia     Energy/Comp do Analyze (energia de deformação)
  trans, rot  Trans e Rot do Nodal Displacements
  N, V, M     do Beam Forces
  flambagem   BLFacs do Buckling Modes
  modelo      Model (gravado só como texto, para conferir nós/elementos)
SAÍDA: texto
"""
import json

import Rhino


def _valor(x, prof=0):
    if prof > 6:
        return str(x)
    if x is None or isinstance(x, (bool, int, float)):
        return x
    if isinstance(x, str):
        return x
    for a in ("Value",):  # GH_Number, GH_String...
        if hasattr(x, a) and not hasattr(x, "X"):
            try:
                return _valor(getattr(x, a), prof + 1)
            except Exception:
                pass
    if hasattr(x, "X") and hasattr(x, "Y") and hasattr(x, "Z"):
        return [float(x.X), float(x.Y), float(x.Z)]
    try:
        return float(x)
    except Exception:
        pass
    try:
        return [_valor(v, prof + 1) for v in x]
    except TypeError:
        return str(x)


def _arvore(t):
    """DataTree -> {"caminhos": [...], "ramos": [[...], ...]} (ou lista simples)."""
    if t is None:
        return None
    if hasattr(t, "Branches") and hasattr(t, "Paths"):
        return {"caminhos": [str(p) for p in t.Paths], "ramos": [_valor(list(b)) for b in t.Branches]}
    return _valor(t)


def _texto(t):
    try:
        return [str(v) for b in t.Branches for v in b]
    except Exception:
        return str(t)


doc = Rhino.RhinoDoc.ActiveDoc
saida = {
    "nome": None,
    "unidade_modelo": str(doc.ModelUnitSystem),
    "escala_para_m": Rhino.RhinoMath.UnitScale(doc.ModelUnitSystem, Rhino.UnitSystem.Meters),
}
try:
    saida["nome"] = _texto(nome)[0]
except Exception:
    saida["nome"] = str(nome)
for chave in ("massa", "desloc_max", "energia", "trans", "rot", "N", "V", "M", "flambagem"):
    v = globals().get(chave)
    if v is not None:
        saida[chave] = _arvore(v)
if globals().get("modelo") is not None:
    saida["modelo"] = _texto(modelo)
texto = json.dumps(saida)

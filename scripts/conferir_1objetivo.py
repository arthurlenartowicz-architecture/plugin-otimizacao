"""Confere o resultado da otimização com 1 objetivo feita no Rhino (4f1).

    .venv\\Scripts\\python.exe scripts\\conferir_1objetivo.py            (caso sala_1obj)
    .venv\\Scripts\\python.exe scripts\\conferir_1objetivo.py <caso>

Lê resultados/<caso>/resultado.json (gravado pela definição do
rhino/grasshopper/conferir_1objetivo.py) e confere:
  1. algoritmo = ga, 1 objetivo (sol_luz.da, max), todas as avaliações pedidas;
  2. até 20 soluções distintas, do maior DA ao menor;
  3. reavaliação independente: as soluções montadas pelo protótipo em Python
     (`prototipos.predio_de`, a mesma sala) dão o mesmo DA (a malha do GH é outra,
     então tolerância de 2 pontos, média 0,5) e o melhor
     continua entre os melhores;
  4. o GA não perde da busca aleatória com o mesmo orçamento nem do maior DA
     da otimização de 3 objetivos (resultados/sala_gh), se ela existir.
"""
import json
import sys
from pathlib import Path

import numpy as np

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "src"))
sys.path.insert(0, str(RAIZ))

# pontos percentuais: a malha do GH é outra (sensores em outros lugares, ~2% no nível
# rápido); o ruído entre lotes não existe mais (semente por variante, ver CLAUDE.md)
TOL_DA_MAX, TOL_DA_MEDIA = 2.0, 0.5


def _sala(p):
    from otimiza.nucleo.geometria import caixa
    from otimiza.nucleo.prototipos import predio_de
    q = {k: float(p[k]) for k in ("janela_N", "janela_L", "janela_S", "janela_O",
                                  "rotacao_graus")}
    return predio_de(q, largura=6.0, profundidade=12.0, altura=3.0,
                     contexto=[caixa(-10, 14, 0, 16, 24, 30)])   # = sala_exemplo.py


def main(caso="sala_1obj"):
    import otimiza.modulos.sol_luz  # noqa: F401
    from otimiza.nucleo.clima import ler_epw
    from otimiza.nucleo.motor import Problema
    from otimiza.otimizacao.otimizador import avaliador_motor
    from otimiza.servidor.protocolo import espaco_de

    arq = RAIZ / "resultados" / caso / "resultado.json"
    if not arq.is_file():
        sys.exit(f"não achei {arq}: rode a otimização no Rhino antes")
    d = json.loads(arq.read_text(encoding="utf-8"))
    falhas = []

    def conferir(ok, texto):
        print(("  ok   " if ok else "  FALHA ") + texto)
        if not ok:
            falhas.append(texto)

    info = d["info"]
    nivel = info.get("nivel", "rapido")
    print(f"{arq}\n1. algoritmo e problema")
    conferir(d["algoritmo"] == "ga", f"algoritmo = {d['algoritmo']} (esperado ga)")
    conferir(d["objetivos"] == [dict(nome="sol_luz.da", sentido="max")],
             f"objetivos = {d['objetivos']}")
    pedidas = info["populacao"] * info["geracoes"]
    conferir(d["n_avaliacoes"] == pedidas, f"{d['n_avaliacoes']} avaliações "
             f"({info['populacao']} × {info['geracoes']} = {pedidas}), {d['n_viaveis']} viáveis, "
             f"{info.get('variantes_geradas', '?')} geradas no GH, {info.get('tempo_total_s', '?')} s")

    print("2. soluções exportadas")
    sol = d["solucoes"]
    da = np.array([s["objetivos"]["sol_luz.da"] for s in sol])
    conferir(0 < len(sol) <= 20, f"{len(sol)} soluções (máx. 20)")
    conferir(bool((np.diff(da) <= 0).all()), f"DA do maior ao menor: {da[0]:.2f} … {da[-1]:.2f}")
    chaves = {json.dumps(s["parametros"], sort_keys=True) for s in sol}
    conferir(len(chaves) == len(sol), "todas distintas")
    print("     melhor:", sol[0]["parametros"])

    print(f"3. reavaliação pelo protótipo em Python (nível {nivel})")
    epws = sorted((RAIZ / "data" / "epw").glob("*.epw"))   # o mesmo padrão do servidor
    clima = ler_epw(str(epws[0]))
    av = avaliador_motor(_sala, Problema(["sol_luz.da"], sentidos={"sol_luz.da": "max"}),
                         nivel, clima)
    F, _ = av([s["parametros"] for s in sol])
    da_py = -F[:, 0]
    dif = np.abs(da_py - da)
    conferir(dif.max() < TOL_DA_MAX and dif.mean() < TOL_DA_MEDIA,
             f"|DA GH − protótipo| máx {dif.max():.2f}, média {dif.mean():.2f} pontos "
             f"(tolerância {TOL_DA_MAX} / média {TOL_DA_MEDIA})")
    conferir(da_py.argmax() <= 2 or da_py.max() - da_py[0] < TOL_DA_MAX,
             f"melhor pelo protótipo é a solução {da_py.argmax()} ({da_py.max():.2f}; "
             f"a 0 dá {da_py[0]:.2f})")

    print("4. comparação")
    esp = espaco_de(d["parametros"])
    U = np.random.default_rng(0).random((pedidas, esp.n))
    Fa, _ = av(esp.decodificar(esp.arredondar(U)))
    aleat = -Fa[:, 0].min()                   # F = −DA: o melhor é o menor F
    conferir(da_py[0] >= aleat - TOL_DA_MAX, f"GA {da_py[0]:.2f} × busca aleatória com {pedidas} "
             f"avaliações {aleat:.2f} (protótipo)")
    tres = RAIZ / "resultados" / "sala_gh" / "resultado.json"
    if tres.is_file():
        d3 = json.loads(tres.read_text(encoding="utf-8"))
        if any(o["nome"] == "sol_luz.da" for o in d3["objetivos"]):
            m3 = max(s["objetivos"]["sol_luz.da"] for s in d3["solucoes"])
            conferir(da[0] >= m3 - TOL_DA_MAX, f"GA {da[0]:.2f} × maior DA da otimização de 3 "
                     f"objetivos ({d3['algoritmo']}, sala_gh) {m3:.2f}")

    print("\nTUDO CERTO" if not falhas else f"\n{len(falhas)} FALHA(S)")
    return not falhas


if __name__ == "__main__":
    sys.exit(0 if main(*sys.argv[1:]) else 1)

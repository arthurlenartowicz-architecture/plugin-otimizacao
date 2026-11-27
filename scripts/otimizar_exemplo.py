"""Exemplos da Etapa 3: otimiza e grava a frente de Pareto em resultados/<caso>/.

    .venv\\Scripts\\python.exe scripts\\otimizar_exemplo.py trelica
    .venv\\Scripts\\python.exe scripts\\otimizar_exemplo.py sala      (precisa de um EPW em data/epw)

Saída: resultados/<caso>/resultado.json (lido pelo componente do Grasshopper
rhino/grasshopper/pareto_rhino.py) e resultados/<caso>/paralelas.png.
"""
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "src"))

from otimiza.nucleo.motor import Problema  # noqa: E402
from otimiza.nucleo.parametros import Categorico, Continuo, Espaco, Inteiro  # noqa: E402
from otimiza.otimizacao import otimizar_problema  # noqa: E402
from otimiza.otimizacao.pareto import exportar  # noqa: E402


def trelica():
    import otimiza.modulos.estrutura  # noqa: F401
    from otimiza.modulos.estrutura.prototipos import TUBOS, trelica_pratt_de
    espaco = Espaco([Continuo("altura", 0.6, 3.0), Inteiro("n_paineis", 4, 10),
                     Categorico("secao", tuple(TUBOS))])
    problema = Problema(["estrutura.massa", "estrutura.deslocamento_max"],
                        restricoes=[("estrutura.utilizacao", "<=", 100)])
    r = otimizar_problema(espaco, trelica_pratt_de, problema, "padrao", populacao=60, geracoes=30)
    return r, trelica_pratt_de


def sala():
    import otimiza.modulos.sol_luz  # noqa: F401
    from otimiza.nucleo.clima import ler_epw
    from otimiza.nucleo.geometria import caixa
    from otimiza.nucleo.prototipos import predio_de
    epws = sorted((RAIZ / "data" / "epw").glob("*.epw"))
    if not epws:
        raise SystemExit("coloque um arquivo .epw em data/epw")
    torre = caixa(-10, 14, 0, 16, 24, 30)   # vizinho alto ao norte

    def gerador(p):
        return predio_de(p, profundidade=12.0, contexto=[torre])

    espaco = Espaco([Continuo(f"janela_{f}", 0.0, 0.8) for f in "NLSO"]
                    + [Continuo("rotacao_graus", -45, 45)])
    problema = Problema(["sol_luz.da", "sol_luz.udi", "sol_luz.ase"])
    r = otimizar_problema(espaco, gerador, problema, "rapido", clima=ler_epw(epws[0]),
                          populacao=48, geracoes=20)
    return r, gerador


CASOS = {"trelica": trelica, "sala": sala}

if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    caso = sys.argv[1] if len(sys.argv) > 1 else "trelica"
    if caso not in CASOS:
        raise SystemExit(f"caso desconhecido {caso!r}; use um de {sorted(CASOS)}")
    resultado, gerador = CASOS[caso]()
    pasta = RAIZ / "resultados" / caso
    dados = exportar(resultado, str(pasta), gerador=gerador)
    print(f"{caso}: {resultado.algoritmo}, {dados['n_avaliacoes']} avaliações em "
          f"{resultado.tempo:.1f} s; {len(dados['solucoes'])} soluções na frente -> {pasta}")

"""Gera os casos de entrada da validação contra o Karamba (testes/referencias/karamba/).

    .venv\\Scripts\\python.exe scripts\\gerar_casos_karamba.py

No Grasshopper, o componente "Caso Karamba" lê entrada_<nome>.json e alimenta o
Karamba; o exportador grava karamba_<nome>.json na mesma pasta.
Seção = tubo RO114.3/4 e aço S235, os padrões do Karamba.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from otimiza.modulos.estrutura.arquivo import para_dict
from otimiza.modulos.estrutura.fem import Estrutura, tubo_circular
from otimiza.modulos.estrutura.prototipos import portico_espacial, trelica_espacial  # noqa: E402

PASTA = Path(__file__).resolve().parents[1] / "testes" / "referencias" / "karamba"
TUBO = (0.1143, 0.004)


def casos() -> dict[str, Estrutura]:
    sec = tubo_circular(*TUBO)
    port = portico_espacial(vao_x=5.0, vao_y=4.0, pe_direito=3.0, n_pav=2, carga_viga=0.0,
                            vento=0.0, pilar=sec, viga=sec, gravidade=0.0)
    # cargas nodais assimétricas: vertical nos 4 nós do topo, horizontais em x e y
    # (torção do conjunto, flexão nos dois planos) e um momento num nó
    port.cargas[8:12, 2] = -15.0
    port.cargas[4, 0] = 4.0
    port.cargas[10, 1] = 3.0
    port.cargas[6, 3] = 1.5
    tre = trelica_espacial(nx=4, ny=3, modulo=2.0, altura=1.5, carga=8.0, secao=sec)
    return {"portico": port, "trelica": tre}


def main():
    PASTA.mkdir(parents=True, exist_ok=True)
    for nome, s in casos().items():
        caminho = PASTA / f"entrada_{nome}.json"
        caminho.write_text(json.dumps(para_dict(s, nome, TUBO), indent=1), encoding="utf-8")
        print("gravado", caminho)


if __name__ == "__main__":
    main()

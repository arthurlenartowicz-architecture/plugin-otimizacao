from pathlib import Path

import pytest

DIAS_MES = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
CABECALHO = [
    "LOCATION,Cidade Teste,SP,BRA,Sintetico,000000,-23.55,-46.63,-3.0,760.0",
    "DESIGN CONDITIONS,0",
    "TYPICAL/EXTREME PERIODS,0",
    "GROUND TEMPERATURES,0",
    "HOLIDAYS/DAYLIGHT SAVINGS,No,0,0,0",
    "COMMENTS 1,arquivo sintetico para testes",
    "COMMENTS 2,",
    "DATA PERIODS,1,1,Data,Sunday, 1/ 1,12/31",
]


def escrever_epw_sintetico(caminho: Path, n_linhas: int = 8760) -> Path:
    """EPW falso com valores conhecidos: temperatura = (i % 400) / 10, GHI = i % 1000."""
    linhas = list(CABECALHO)
    i = 0
    for m, n_dias in enumerate(DIAS_MES, start=1):
        for d in range(1, n_dias + 1):
            for h in range(1, 25):
                if i >= n_linhas:
                    break
                campos = ["0"] * 35
                campos[0:6] = ["1999", str(m), str(d), str(h), "60", "?9?9?9?9E0"]
                campos[6] = f"{(i % 400) / 10:.1f}"
                campos[13] = str(i % 1000)
                linhas.append(",".join(campos))
                i += 1
    caminho.write_text("\n".join(linhas) + "\n", encoding="latin-1")
    return caminho


@pytest.fixture
def epw_sintetico(tmp_path):
    return escrever_epw_sintetico(tmp_path / "teste.epw")


PASTA_EPW = Path(__file__).resolve().parents[1] / "data" / "epw"
EPWS_REAIS = sorted(PASTA_EPW.glob("*.epw"))

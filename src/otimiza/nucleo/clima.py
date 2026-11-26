"""Leitura de arquivos climáticos EPW (EnergyPlus Weather).

Um EPW tem 8 linhas de cabeçalho e 8760 linhas de dados (uma por hora de um
ano não bissexto). A linha da hora `h` (1..24) descreve o intervalo que vai de
h-1 até h, em hora local padrão (sem horário de verão).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import torch

HORAS_ANO = 8760
LINHAS_CABECALHO = 8

# nome do campo -> coluna do EPW (contando a partir de 0)
CAMPOS = {
    "temp_bulbo_seco": 6,        # °C
    "temp_orvalho": 7,           # °C
    "umidade_relativa": 8,       # %
    "pressao": 9,                # Pa
    "rad_infravermelha_horiz": 12,  # Wh/m²
    "ghi": 13,                   # radiação global horizontal, Wh/m²
    "dni": 14,                   # radiação direta normal, Wh/m²
    "dhi": 15,                   # radiação difusa horizontal, Wh/m²
    "ilum_global_horiz": 16,     # lux
    "ilum_direta_normal": 17,    # lux
    "ilum_difusa_horiz": 18,     # lux
    "vento_direcao": 20,         # graus a partir do norte
    "vento_velocidade": 21,      # m/s
    "cobertura_ceu": 22,         # décimos
    "cobertura_opaca": 23,       # décimos
}


@dataclass
class Local:
    cidade: str
    estado: str
    pais: str
    fonte: str
    wmo: str
    latitude: float   # graus, positivo ao norte
    longitude: float  # graus, positivo a leste
    fuso: float       # horas em relação ao UTC (ex.: -3 para Brasília)
    elevacao: float   # metros


@dataclass
class Clima:
    local: Local
    mes: torch.Tensor   # (8760,) int64
    dia: torch.Tensor   # (8760,) int64
    hora: torch.Tensor  # (8760,) int64, 1..24 = fim do intervalo
    dados: dict[str, torch.Tensor]  # cada um (8760,) float64

    def __getitem__(self, nome: str) -> torch.Tensor:
        return self.dados[nome]

    @property
    def hora_meio(self) -> torch.Tensor:
        """Meio de cada intervalo horário (0.5..23.5), usado para a posição solar."""
        return self.hora.to(torch.float64) - 0.5


def _ler_texto(caminho: Path) -> str:
    # EPWs brasileiros costumam vir em latin-1 (ex.: "São Paulo").
    try:
        return caminho.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return caminho.read_text(encoding="latin-1")


def ler_epw(caminho: str | Path) -> Clima:
    caminho = Path(caminho)
    linhas = _ler_texto(caminho).splitlines()

    if not linhas or not linhas[0].startswith("LOCATION"):
        raise ValueError(f"{caminho.name}: a primeira linha não é 'LOCATION'; não parece um EPW.")
    loc = linhas[0].split(",")
    local = Local(
        cidade=loc[1].strip(), estado=loc[2].strip(), pais=loc[3].strip(),
        fonte=loc[4].strip(), wmo=loc[5].strip(),
        latitude=float(loc[6]), longitude=float(loc[7]),
        fuso=float(loc[8]), elevacao=float(loc[9]),
    )

    tabela = [l.split(",") for l in linhas[LINHAS_CABECALHO:] if l.strip()]
    if len(tabela) != HORAS_ANO:
        raise ValueError(
            f"{caminho.name}: {len(tabela)} linhas de dados; esperado {HORAS_ANO} "
            "(ano não bissexto, passo horário)."
        )
    col_max = max(CAMPOS.values())
    for i, l in enumerate(tabela):
        if len(l) <= col_max:
            raise ValueError(f"{caminho.name}: linha de dados {i + 1} tem só {len(l)} campos.")

    tempo = torch.tensor([[int(l[1]), int(l[2]), int(l[3])] for l in tabela], dtype=torch.int64)
    colunas = list(CAMPOS.values())
    valores = torch.tensor([[float(l[c]) for c in colunas] for l in tabela], dtype=torch.float64)

    hora = tempo[:, 2]
    if hora.min() < 1 or hora.max() > 24:
        raise ValueError(f"{caminho.name}: horas fora do intervalo 1..24.")

    dados = {nome: valores[:, j] for j, nome in enumerate(CAMPOS)}
    return Clima(local=local, mes=tempo[:, 0], dia=tempo[:, 1], hora=hora, dados=dados)

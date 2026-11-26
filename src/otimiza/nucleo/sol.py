"""Posição solar pelo algoritmo da NOAA (Meeus, "Astronomical Algorithms").

Precisão de ~0,01° entre 1800 e 2100, suficiente para estudos de insolação.
Tudo é calculado em lote (um tensor com todas as horas de uma vez) e em
float64: em float32 o dia juliano (~2,46 milhões) perderia frações de hora.

Convenções
- altitude: graus acima do horizonte (com refração atmosférica, se pedida)
- azimute: graus a partir do norte, no sentido horário (90 = leste)
- vetores: direção do ponto PARA o sol, eixos do Rhino (x = leste, y = norte,
  z = cima). O Ladybug usa o vetor oposto (do sol para o chão).
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import torch

from otimiza.nucleo.clima import Clima

ANO_PADRAO = 2017  # ano não bissexto fixo (o mesmo que o Ladybug usa para EPW)


@dataclass
class PosicaoSolar:
    altitude: torch.Tensor       # graus
    azimute: torch.Tensor        # graus, do norte, horário
    declinacao: torch.Tensor     # graus
    equacao_tempo: torch.Tensor  # minutos

    @property
    def zenite(self) -> torch.Tensor:
        return 90.0 - self.altitude

    @property
    def acima_horizonte(self) -> torch.Tensor:
        return self.altitude > 0

    @property
    def vetores(self) -> torch.Tensor:
        """(N, 3) vetores unitários apontando para o sol."""
        alt = torch.deg2rad(self.altitude)
        az = torch.deg2rad(self.azimute)
        return torch.stack(
            [torch.cos(alt) * torch.sin(az), torch.cos(alt) * torch.cos(az), torch.sin(alt)],
            dim=-1,
        )


def dia_juliano(ano, mes, dia, hora_utc) -> torch.Tensor:
    """Dia juliano (Meeus, cap. 7). Aceita tensores; hora_utc em horas decimais."""
    ajuste = mes <= 2
    a = torch.where(ajuste, ano - 1, ano)
    m = torch.where(ajuste, mes + 12, mes)
    A = torch.floor(a / 100)
    B = 2 - A + torch.floor(A / 4)
    return (
        torch.floor(365.25 * (a + 4716)) + torch.floor(30.6001 * (m + 1))
        + dia + B - 1524.5 + hora_utc / 24.0
    )


def _refracao(altitude: torch.Tensor, pressao_mbar: float, temperatura_c: float) -> torch.Tensor:
    """Correção de refração da NOAA, em graus, ajustada por pressão e temperatura."""
    t = torch.tan(torch.deg2rad(altitude))
    r = torch.where(
        altitude > 85, torch.zeros_like(altitude),
        torch.where(
            altitude > 5, 58.1 / t - 0.07 / t**3 + 0.000086 / t**5,
            torch.where(
                altitude > -0.575,
                1735 + altitude * (-518.2 + altitude * (103.4 + altitude * (-12.79 + altitude * 0.711))),
                -20.772 / t,
            ),
        ),
    )
    fator = (pressao_mbar / 1010.0) * (283.0 / (273.0 + temperatura_c))
    return r / 3600.0 * fator


def posicao_solar(
    latitude: float,
    longitude: float,
    fuso: float,
    mes,
    dia,
    hora,
    ano=ANO_PADRAO,
    refracao: bool = True,
    pressao_mbar: float = 1010.0,
    temperatura_c: float = 10.0,
    device=None,
) -> PosicaoSolar:
    """Posição do sol em hora local padrão. mes, dia e hora podem ser tensores."""
    f = lambda v: torch.as_tensor(v, dtype=torch.float64, device=device)
    mes, dia, hora, ano = torch.broadcast_tensors(f(mes), f(dia), f(hora), f(ano))

    T = (dia_juliano(ano, mes, dia, hora - fuso) - 2451545.0) / 36525.0  # séculos desde J2000

    # órbita aparente do sol
    L0 = torch.remainder(280.46646 + T * (36000.76983 + T * 0.0003032), 360.0)
    M = torch.deg2rad(357.52911 + T * (35999.05029 - 0.0001537 * T))
    e = 0.016708634 - T * (0.000042037 + 0.0000001267 * T)
    C = (
        torch.sin(M) * (1.914602 - T * (0.004817 + 0.000014 * T))
        + torch.sin(2 * M) * (0.019993 - 0.000101 * T)
        + torch.sin(3 * M) * 0.000289
    )
    omega = torch.deg2rad(125.04 - 1934.136 * T)
    long_aparente = torch.deg2rad(L0 + C - 0.00569 - 0.00478 * torch.sin(omega))

    obliq_media = 23 + (26 + (21.448 - T * (46.815 + T * (0.00059 - T * 0.001813))) / 60) / 60
    obliq = torch.deg2rad(obliq_media + 0.00256 * torch.cos(omega))
    dec = torch.asin(torch.sin(obliq) * torch.sin(long_aparente))

    # equação do tempo (minutos)
    y = torch.tan(obliq / 2) ** 2
    L0r = torch.deg2rad(L0)
    eot = 4 * torch.rad2deg(
        y * torch.sin(2 * L0r)
        - 2 * e * torch.sin(M)
        + 4 * e * y * torch.sin(M) * torch.cos(2 * L0r)
        - 0.5 * y**2 * torch.sin(4 * L0r)
        - 1.25 * e**2 * torch.sin(2 * M)
    )

    # ângulo horário a partir do tempo solar verdadeiro
    tempo_solar = torch.remainder(hora * 60 + eot + 4 * longitude - 60 * fuso, 1440.0)
    ha = torch.deg2rad(tempo_solar / 4 - 180)

    lat = math.radians(latitude)
    cos_z = math.sin(lat) * torch.sin(dec) + math.cos(lat) * torch.cos(dec) * torch.cos(ha)
    altitude = 90.0 - torch.rad2deg(torch.acos(cos_z.clamp(-1.0, 1.0)))
    azimute = torch.remainder(
        torch.rad2deg(torch.atan2(
            torch.sin(ha), torch.cos(ha) * math.sin(lat) - torch.tan(dec) * math.cos(lat)
        )) + 180.0,
        360.0,
    )
    if refracao:
        altitude = altitude + _refracao(altitude, pressao_mbar, temperatura_c)

    return PosicaoSolar(altitude, azimute, torch.rad2deg(dec), eot)


def posicao_solar_clima(clima: Clima, meio_hora: bool = True, **kw) -> PosicaoSolar:
    """As 8760 posições do ano do EPW. Por padrão no meio de cada hora (h - 0,5)."""
    hora = clima.hora_meio if meio_hora else clima.hora.to(torch.float64)
    loc = clima.local
    return posicao_solar(loc.latitude, loc.longitude, loc.fuso, clima.mes, clima.dia, hora, **kw)

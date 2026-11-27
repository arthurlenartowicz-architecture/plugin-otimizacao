"""Gera os dados de conforto e o gabarito do módulo termico (Etapa 4a) com o Ladybug.

Rodar com o Python do Ladybug Tools (não o da .venv):
    "C:\\Program Files\\ladybug_tools\\python\\python.exe" scripts\\gerar_ref_termico.py

Saídas:
- src/otimiza/modulos/termico/dados_conforto.json: coeficientes do polinômio do UTCI
  (Bröde et al. 2009, o mesmo do Fortran do utci.org) extraídos exatamente da função
  do ladybug_comfort (passando "variáveis simbólicas" para ela), e o fator de projeção
  do SolarCal (pessoa em pé, sol a 135° = de lado/costas, altitude 1..90°).
- testes/referencias/curitiba/termico_ladybug.json: UTCI em pontos sorteados e, para
  três situações (campo aberto; meia sombra; sombra total), MRT e UTCI hora a hora do
  LB Outdoor Solar MRT + LB UTCI Comfort (OutdoorSolarCal + UTCI) com o EPW de Curitiba.
"""
import json
import os
import random

from ladybug.epw import EPW
from ladybug_comfort import utci as m_utci
from ladybug_comfort.collection.solarcal import OutdoorSolarCal
from ladybug_comfort.collection.utci import UTCI
from ladybug_comfort.solarcal import SOLARCAL_SPLINES

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EPW_CAMINHO = os.path.join(RAIZ, "data", "epw", "BRA_PR_Curitiba.838420_INMET.epw")


class Poli(object):
    """Polinômio em 4 variáveis: {(e_ta, e_dtr, e_vel, e_pa): coef}."""

    def __init__(self, termos):
        self.t = {k: v for k, v in termos.items() if v != 0}

    @staticmethod
    def var(i):
        e = [0, 0, 0, 0]
        e[i] = 1
        return Poli({tuple(e): 1.0})

    @staticmethod
    def _p(x):
        return x if isinstance(x, Poli) else Poli({(0, 0, 0, 0): float(x)})

    def __add__(self, o):
        o = self._p(o)
        r = dict(self.t)
        for k, v in o.t.items():
            r[k] = r.get(k, 0.0) + v
        return Poli(r)

    __radd__ = __add__

    def __neg__(self):
        return Poli({k: -v for k, v in self.t.items()})

    def __sub__(self, o):
        return self + (-self._p(o))

    def __rsub__(self, o):
        return self._p(o) - self

    def __mul__(self, o):
        o = self._p(o)
        r = {}
        for k1, v1 in self.t.items():
            for k2, v2 in o.t.items():
                k = tuple(a + b for a, b in zip(k1, k2))
                r[k] = r.get(k, 0.0) + v1 * v2
        return Poli(r)

    __rmul__ = __mul__

    def __pow__(self, n):
        r = Poli({(0, 0, 0, 0): 1.0})
        for _ in range(int(n)):
            r = r * self
        return r


def coeficientes_utci():
    p = m_utci._utci_polynomial(*[Poli.var(i) for i in range(4)])
    termos = sorted(p.t.items())
    # confere com a função original em pontos sorteados
    rnd = random.Random(1)
    for _ in range(50):
        x = [rnd.uniform(-40, 45), rnd.uniform(-20, 60), rnd.uniform(0.5, 17), rnd.uniform(0, 4)]
        a = m_utci._utci_polynomial(*x)
        b = sum(c * x[0] ** e[0] * x[1] ** e[1] * x[2] ** e[2] * x[3] ** e[3] for e, c in termos)
        assert abs(a - b) < 1e-8 * max(1.0, abs(a)), (a, b)
    return [list(e) for e, _ in termos], [c for _, c in termos]


def main():
    expo, coef = coeficientes_utci()
    proj = [float(v) for v in SOLARCAL_SPLINES["standing"][135]]
    dados = {
        "fonte": "ladybug_comfort (utci._utci_polynomial; SOLARCAL_SPLINES standing, sharp 135)",
        "utci_expoentes": expo,   # [ta, d_tr, vel, pa(kPa)]
        "utci_coef": coef,
        "solarcal_proj_135": proj,  # índice = ceil(altitude) - 1
    }
    destino = os.path.join(RAIZ, "src", "otimiza", "modulos", "termico", "dados_conforto.json")
    with open(destino, "w") as f:
        json.dump(dados, f)
    print("termos do UTCI:", len(coef), "->", destino)

    rnd = random.Random(2)
    pontos = []
    for _ in range(300):
        ta, rh = rnd.uniform(-30, 45), rnd.uniform(5, 100)
        tr, vel = ta + rnd.uniform(-20, 50), rnd.uniform(0, 20)
        pontos.append([ta, tr, vel, rh, m_utci.universal_thermal_climate_index(ta, tr, vel, rh)])

    epw = EPW(EPW_CAMINHO)
    casos = []
    for sky, fract in ((1.0, 1.0), (0.5, 0.5), (0.2, 0.0)):
        s = OutdoorSolarCal(epw.location, epw.direct_normal_radiation,
                            epw.diffuse_horizontal_radiation, epw.horizontal_infrared_radiation_intensity,
                            epw.dry_bulb_temperature, fraction_body_exposed=fract,
                            sky_exposure=sky, floor_reflectance=0.25)
        alt, _ = s._get_altitudes_and_sharps()
        u = UTCI(epw.dry_bulb_temperature, epw.relative_humidity, s.mean_radiant_temperature,
                 epw.wind_speed)
        casos.append({
            "sky_exposure": sky, "fract_exposed": fract,
            "altitude": [round(a, 5) for a in alt],
            "mrt": [round(v, 5) for v in s.mean_radiant_temperature.values],
            "utci": [round(v, 5) for v in u.universal_thermal_climate_index.values],
            "conforto_pct": u.percent_neutral, "calor_pct": u.percent_hot, "frio_pct": u.percent_cold,
        })
        print("céu %.1f, corpo %.1f: conforto %.2f%%, calor %.2f%%, frio %.2f%%" % (
            sky, fract, u.percent_neutral, u.percent_hot, u.percent_cold))
    dt = epw.dry_bulb_temperature.datetimes
    # séries exatamente como o Ladybug as usou (o leitor do LB não alinha todos os campos
    # do EPW do mesmo jeito: a temperatura vem deslocada uma hora, a radiação não)
    entradas = {k: list(getattr(epw, a).values) for k, a in (
        ("ta", "dry_bulb_temperature"), ("rh", "relative_humidity"), ("vento", "wind_speed"),
        ("ir", "horizontal_infrared_radiation_intensity"), ("dhi", "diffuse_horizontal_radiation"),
        ("dni", "direct_normal_radiation"))}
    ref = {"epw": os.path.basename(EPW_CAMINHO), "ground_reflectance": 0.25,
           "datetimes_inicio": [str(d) for d in dt[:3]], "entradas": entradas,
           "utci_pontos": pontos, "casos": casos}
    destino = os.path.join(RAIZ, "testes", "referencias", "curitiba", "termico_ladybug.json")
    with open(destino, "w") as f:
        json.dump(ref, f)
    print("gabarito ->", destino, "| 1as horas do LB:", ref["datetimes_inicio"])


if __name__ == "__main__":
    main()

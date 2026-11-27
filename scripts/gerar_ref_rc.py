"""Gabarito do modelo RC (Etapa 4b): sala do Honeybee simulada no EnergyPlus.

Rodar com o Python do Ladybug Tools (traz honeybee_energy e o EnergyPlus do OpenStudio):
    "C:\\Program Files\\ladybug_tools\\python\\python.exe" scripts\\gerar_ref_rc.py

Sala 6 × 8 × 3 m (a da 1c), construções genéricas do Honeybee, EPW de Curitiba.
Cargas internas, infiltração e termostato explícitos (os mesmos que o teste passa ao nosso
modelo, gravados no json): pessoas 0,0565 p/m² × 120 W (sem latente, 30% radiante),
iluminação 10 W/m² (32% radiante + 25% visível), equipamentos 10 W/m² (50% radiante),
de segunda a sexta 8–18 h (iluminação/equipamentos 5% fora do horário), infiltração
constante de 0,5 trocas/h, sem ventilação. Termostato 20/24 °C o tempo todo, cargas
ideais sem economizador nem recuperação (só sensível).

Casos: A (janela N 40%, piso adiabático), B (janelas N 40% + O 30%, piso no solo com a
temperatura do solo do cabeçalho do EPW a 0,5 m), e os dois em evolução livre (sem
climatização: temperatura do ar e operativa hora a hora).
Saída: testes/referencias/rc/eplus_rc.json.
"""
import datetime
import json
import os
import shutil

from honeybee.altnumber import no_limit
from honeybee.boundarycondition import boundary_conditions as bcs
from honeybee.model import Model
from honeybee.room import Room
from honeybee_energy.hvac.idealair import IdealAirSystem
from honeybee_energy.lib.constructionsets import generic_construction_set as cs
from honeybee_energy.load.equipment import ElectricEquipment
from honeybee_energy.load.infiltration import Infiltration
from honeybee_energy.load.lighting import Lighting
from honeybee_energy.load.people import People
from honeybee_energy.load.setpoint import Setpoint
from ladybug.sql import SQLiteResult
from honeybee_energy.run import run_idf
from honeybee_energy.schedule.fixedinterval import ScheduleFixedInterval
from honeybee_energy.schedule.ruleset import ScheduleRuleset
from honeybee_energy.simulation.parameter import SimulationParameter
from honeybee_energy.writer import energyplus_idf_version
from ladybug.epw import EPW

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EPW_CAMINHO = os.path.join(RAIZ, "data", "epw", "BRA_PR_Curitiba.838420_INMET.epw")
PASTA_SIM = os.path.join(os.environ.get("TEMP", RAIZ), "otimiza_eplus_rc")
SAIDA = os.path.join(RAIZ, "testes", "referencias", "rc", "eplus_rc.json")

W, D, H = 6.0, 8.0, 3.0
ACH = 0.5
CARGAS = {"pessoas_m2": 0.0565, "w_pessoa": 120.0, "rad_pessoas": 0.3,
          "iluminacao_w_m2": 10.0, "rad_iluminacao": 0.32 + 0.25,
          "equipamentos_w_m2": 10.0, "rad_equipamentos": 0.5,
          "fora_do_horario": 0.05, "aquecimento": 20.0, "resfriamento": 24.0, "ach": ACH}


def agenda_ocupacao():
    """8760 valores (hora h do EPW = intervalo h−1..h, ano 2017): seg–sex 8–18 h."""
    occ = []
    t0 = datetime.datetime(2017, 1, 1)
    for i in range(8760):
        t = t0 + datetime.timedelta(hours=i)  # início do intervalo
        occ.append(1.0 if t.weekday() < 5 and 8 <= t.hour < 18 else 0.0)
    return occ


def montar_sala(nome, janelas, piso, livre):
    sala = Room.from_box(nome, W, D, H)
    sala.properties.energy.construction_set = cs
    for f in sala.faces:
        n = f.normal
        if abs(n.z) < 0.5:
            o = "N" if n.y > 0.7 else "S" if n.y < -0.7 else "L" if n.x > 0.7 else "O"
            if janelas.get(o):
                f.apertures_by_ratio(janelas[o])
        elif n.z < -0.5:
            if piso == "adiabatico":
                f.boundary_condition = bcs.adiabatic
                f.properties.energy.construction = cs.floor_set.interior_construction
            else:
                f.boundary_condition = bcs.ground
    occ = agenda_ocupacao()
    fora = CARGAS["fora_do_horario"]
    s_occ = ScheduleFixedInterval("ocupacao", occ)
    s_luz = ScheduleFixedInterval("luz_equip", [max(v, fora) for v in occ])
    s_on = ScheduleRuleset.from_constant_value("sempre", 1)
    s_ativ = ScheduleRuleset.from_constant_value("atividade", CARGAS["w_pessoa"])
    e = sala.properties.energy
    e.people = People("pessoas", CARGAS["pessoas_m2"], s_occ, s_ativ,
                      radiant_fraction=CARGAS["rad_pessoas"], latent_fraction=0)
    e.lighting = Lighting("iluminacao", CARGAS["iluminacao_w_m2"], s_luz, return_air_fraction=0,
                          radiant_fraction=0.32, visible_fraction=0.25)
    e.electric_equipment = ElectricEquipment("equip", CARGAS["equipamentos_w_m2"], s_luz,
                                             radiant_fraction=CARGAS["rad_equipamentos"],
                                             latent_fraction=0, lost_fraction=0)
    area_ext = sum(f.area for f in sala.faces if isinstance(f.boundary_condition, type(bcs.outdoors)))
    e.infiltration = Infiltration("infiltracao", ACH * W * D * H / 3600.0 / area_ext, s_on)
    if not livre:
        e.setpoint = Setpoint("termostato",
                              ScheduleRuleset.from_constant_value("aquec", CARGAS["aquecimento"]),
                              ScheduleRuleset.from_constant_value("resfr", CARGAS["resfriamento"]))
        e.hvac = IdealAirSystem("ideal", economizer_type="NoEconomizer",
                                demand_controlled_ventilation=False,
                                sensible_heat_recovery=0, latent_heat_recovery=0,
                                heating_limit=no_limit, cooling_limit=no_limit)
    return sala, area_ext


def temp_solo(epw):
    return [float(v) for v in epw.monthly_ground_temperature[0.5].values]


def simular(caso, epw):
    sala, area_ext = montar_sala(caso["nome"], caso["janelas"], caso["piso"], caso["livre"])
    modelo = Model(caso["nome"], rooms=[sala])
    sim = SimulationParameter()
    sim.output.reporting_frequency = "Hourly"
    for o in ("Zone Ideal Loads Zone Sensible Heating Energy",
              "Zone Ideal Loads Zone Sensible Cooling Energy",
              "Zone Mean Air Temperature", "Zone Operative Temperature",
              "Enclosure Windows Total Transmitted Solar Radiation Energy",
              "Zone Infiltration Sensible Heat Loss Energy", "Zone Infiltration Sensible Heat Gain Energy",
              "Zone Infiltration Current Density Air Change Rate",
              "Surface Inside Face Conduction Heat Transfer Energy",
              "Surface Window Heat Gain Energy", "Surface Window Heat Loss Energy",
              "Surface Outside Face Solar Radiation Heat Gain Energy",
              "Surface Outside Face Net Thermal Radiation Heat Gain Energy"):
        sim.output.add_output(o)
    sim.terrain_type = "City"
    sim.simulation_control.do_zone_sizing = False
    sim.simulation_control.do_system_sizing = False
    sim.simulation_control.do_plant_sizing = False
    pasta = os.path.join(PASTA_SIM, caso["nome"])
    if os.path.isdir(pasta):
        shutil.rmtree(pasta)
    os.makedirs(pasta)
    idf = "\n\n".join((energyplus_idf_version(), sim.to_idf(), modelo.to.idf(modelo)))
    if caso["piso"] == "solo":  # temperatura do solo do EPW (0,5 m), a mesma do nosso modelo
        idf += "\n\nSite:GroundTemperature:BuildingSurface,\n " + \
            ",\n ".join("%.2f" % t for t in temp_solo(epw)) + ";\n"
    caminho = os.path.join(pasta, "in.idf")
    with open(caminho, "w") as f:
        f.write(idf)
    sql = run_idf(caminho, EPW_CAMINHO, silent=True)[0]
    if sql is None or not os.path.isfile(sql):
        raise RuntimeError("EnergyPlus falhou; veja " + pasta)
    r = SQLiteResult(sql)

    def serie(nome, fator=1.0):
        cols = r.data_collections_by_output_name(nome)
        return [round(v * fator, 4) for v in cols[0].values] if cols else None

    j2kwh = 1.0  # o ladybug.sql já converte para kWh
    out = {
        "aquecimento_kwh": serie("Zone Ideal Loads Zone Sensible Heating Energy", j2kwh),
        "resfriamento_kwh": serie("Zone Ideal Loads Zone Sensible Cooling Energy", j2kwh),
        "temp_ar": serie("Zone Mean Air Temperature"),
        "temp_operativa": serie("Zone Operative Temperature"),
        "solar_janelas_kwh": serie("Enclosure Windows Total Transmitted Solar Radiation Energy", j2kwh),
        "area_ext": area_ext,
    }
    # diagnóstico: total anual (kWh, ou média para taxas) de cada saída por superfície
    diag = {}
    for nome in r.available_outputs:
        for col in r.data_collections_by_output_name(nome):
            chave = nome + " | " + str(col.header.metadata.get("Surface", col.header.metadata.get("Zone", "")))
            vals = col.values
            diag[chave] = round(sum(vals) / (len(vals) if "Rate" in nome or "Temperature" in nome else 1), 3)
    out["diagnostico"] = diag
    tot = {k: sum(v) for k, v in out.items() if isinstance(v, list) and "kwh" in k}
    print(caso["nome"], {k: round(v, 1) for k, v in tot.items()})
    return out


def main():
    epw = EPW(EPW_CAMINHO)
    casos = []
    for nome, jan, piso in (("A", {"N": 0.4}, "adiabatico"), ("B", {"N": 0.4, "O": 0.3}, "solo")):
        for livre in (False, True):
            casos.append({"nome": nome + ("_livre" if livre else ""), "janelas": jan,
                          "piso": piso, "livre": livre})
    for c in casos:
        c["eplus"] = simular(c, epw)
    # conforto adaptativo do Ladybug (ASHRAE 55, 80%: ±3,5 °C, média mensal externa) sobre a
    # operativa do EnergyPlus em evolução livre, e PMV/PPD de Fanger em pontos sorteados
    from ladybug_comfort.collection.adaptive import Adaptive
    from ladybug_comfort.parameter.adaptive import AdaptiveParameter
    from ladybug_comfort.pmv import fanger_pmv
    from ladybug_comfort.clo import schiavon_clo
    import random
    par = AdaptiveParameter(True, 3.5, True)
    for c in casos:
        if not c["livre"]:
            continue
        top = epw.dry_bulb_temperature.duplicate()
        top.values = c["eplus"]["temp_operativa"]
        ad = Adaptive(epw.dry_bulb_temperature, top, comfort_parameter=par)
        c["ladybug_adaptativo"] = {
            "prevalente": [round(v, 5) for v in ad.prevailing_outdoor_temperature.values],
            "neutra": [round(v, 5) for v in ad.neutral_temperature.values],
            "confortavel": list(ad.is_comfortable.values)}
    rnd = random.Random(3)
    pmv = []
    for _ in range(300):
        ta, tr = rnd.uniform(14, 32), rnd.uniform(12, 36)
        vel, rh = rnd.uniform(0.05, 0.8), rnd.uniform(15, 90)
        met, t6 = rnd.uniform(0.9, 2.0), rnd.uniform(-10, 35)
        clo = schiavon_clo(t6)
        r = fanger_pmv(ta, tr, vel, rh, met, clo)
        pmv.append([ta, tr, vel, rh, met, t6, clo, r[0], r[1]])
    w = cs.wall_set.exterior_construction
    constr = {
        "parede": {"u": w.u_factor, "camadas": [[m.thickness, m.conductivity, m.density, m.specific_heat]
                                                for m in w.materials], "alfa": w.materials[0].solar_absorptance},
    }
    ref = {"epw": os.path.basename(EPW_CAMINHO), "sala": [W, D, H], "cargas": CARGAS,
           "ocupacao": agenda_ocupacao(), "temp_solo": temp_solo(epw), "construcoes": constr,
           "pmv_pontos": pmv, "casos": casos}
    if not os.path.isdir(os.path.dirname(SAIDA)):
        os.makedirs(os.path.dirname(SAIDA))
    with open(SAIDA, "w") as f:
        json.dump(ref, f)
    print("->", SAIDA)


if __name__ == "__main__":
    main()

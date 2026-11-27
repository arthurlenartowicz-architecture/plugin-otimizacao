"""Módulo termico (Etapa 4a: objetivos 🟢, direto da geometria e do motor A).

- compacidade, área da envoltória, volume (envoltoria.py)
- WWR total e por orientação (N, L, S, O)
- ganho solar pelos vidros: radiação do motor A nos vidros × fator solar (FS) da variante
- conforto externo (UTCI) em pessoas sobre o entorno (conforto_externo.py)

- modelo RC multicamadas (rc.py, 4b): cargas de aquecimento/resfriamento, EUI, consumo,
  balanço energético zero (com sol_luz.fv), PPD (climatizado) e conforto adaptativo e
  graus-hora (evolução livre). A iluminação desliga nas horas ocupadas na fração do DA
  médio do sol_luz (sensor de luz natural liga/desliga).
"""
from __future__ import annotations

import torch

from otimiza.modulos.sol_luz import motor_a
from otimiza.modulos.sol_luz.modulo import DESLOC_EXTERNO, NIVEIS, SOL_LUZ
from otimiza.modulos.termico import conforto_externo as ce
from otimiza.modulos.termico import conforto_interno as ci
from otimiza.modulos.termico import rc, zona
from otimiza.modulos.termico.envoltoria import ORIENTACOES, envoltoria, orientacao
from otimiza.nucleo.cena import CenaLote, media_por_variante, soma_por_variante
from otimiza.nucleo.ceu import criar_ceu
from otimiza.nucleo.contrato import Metrica, Modulo, registrar
from otimiza.nucleo.geometria import sensores_de_malha

ALTURA_PESSOA = 0.018 + 0.9   # m: meio de uma pessoa de 1,8 m (LB Human to Sky Relation, 1 ponto)
LOTE_PESSOAS = 2048

_GEO = {"compacidade", "area_envoltoria", "volume", "wwr"} | {f"wwr_{o}" for o in ORIENTACOES}
_GANHO = {"ganho_solar", "ganho_solar_piso"} | {f"ganho_solar_{o}" for o in ORIENTACOES}
_UTCI = {"utci_conforto", "utci_calor", "utci_frio"}
_RC_CLIM = {"carga_aquecimento", "carga_resfriamento", "carga_termica", "eui", "consumo",
            "balanco_zero", "ppd"}
_RC_LIVRE = {"conforto_adaptativo", "graus_hora_calor", "graus_hora_frio"}
PROGRAMA = rc.Programa()


class Termico(Modulo):
    nome = "termico"
    depende_de = ("sol_luz",)
    metricas = {
        "compacidade": Metrica("1/m", "min", "área da envoltória ÷ volume"),
        "area_envoltoria": Metrica("m²", "min", "fachadas + vidros + cobertura + base"),
        "volume": Metrica("m³", "max", "volume fechado pela envoltória"),
        "wwr": Metrica("%", "min", "área de vidro ÷ área de parede (todas as orientações)"),
        **{f"wwr_{o}": Metrica("%", "min", f"área de vidro ÷ área de parede ao {o}") for o in ORIENTACOES},
        "ganho_solar": Metrica("kWh/ano", "min", "radiação solar que entra pelos vidros (× FS)"),
        "ganho_solar_piso": Metrica("kWh/m²·ano", "min", "ganho solar ÷ área de piso"),
        **{f"ganho_solar_{o}": Metrica("kWh/m²·ano", "min",
                                       f"ganho solar por m² de vidro ao {o} (0 sem vidro)")
           for o in ORIENTACOES},
        "utci_conforto": Metrica("%", "max", "% das horas do ano com UTCI 9–26 °C no entorno"),
        "utci_calor": Metrica("%", "min", "% das horas do ano com UTCI > 26 °C no entorno"),
        "utci_frio": Metrica("%", "min", "% das horas do ano com UTCI < 9 °C no entorno"),
        "carga_aquecimento": Metrica("kWh/m²·ano", "min", "carga térmica de aquecimento (ideal, sensível)"),
        "carga_resfriamento": Metrica("kWh/m²·ano", "min", "carga térmica de resfriamento (ideal, sensível)"),
        "carga_termica": Metrica("kWh/m²·ano", "min", "aquecimento + resfriamento"),
        "eui": Metrica("kWh/m²·ano", "min", "consumo: cargas ÷ COP + iluminação + equipamentos"),
        "consumo": Metrica("kWh/ano", "min", "consumo total (EUI × área de piso)"),
        "balanco_zero": Metrica("kWh/ano", "max", "geração FV (sol_luz.fv) − consumo"),
        "ppd": Metrica("%", "min", "PPD médio nas horas ocupadas (climatizado)"),
        "conforto_adaptativo": Metrica("%", "max", "% das horas ocupadas no conforto adaptativo "
                                       "ASHRAE 55 (80%), sem climatização"),
        "graus_hora_calor": Metrica("°C·h", "min", "graus-hora acima da faixa adaptativa (ocupadas)"),
        "graus_hora_frio": Metrica("°C·h", "min", "graus-hora abaixo da faixa adaptativa (ocupadas)"),
    }

    def precisa(self, metricas):
        r = {}
        if metricas & (_RC_CLIM | _RC_LIVRE):
            r["sol_luz"] = {"da"}
        if "balanco_zero" in metricas:
            r.setdefault("sol_luz", set()).add("fv")
        return r

    def avaliar(self, cena: CenaLote, cache, metricas: set[str], nivel: str = "padrao"):
        cfg = NIVEIS[nivel]
        r = {}
        if metricas & (_GEO | {"ganho_solar_piso"}):
            r.update(self._geometria(cena))
        if metricas & _GANHO:
            r.update(self._ganho(cena, cache, cfg, r))
        if metricas & _UTCI:
            r.update(self._utci(cena, cache, cfg))
        if metricas & (_RC_CLIM | _RC_LIVRE):
            r.update(self._rc(cena, cache, cfg, metricas))
        return {m: r[m].to(cena.device) for m in metricas}

    # ------------------------------------------------------------------ modelo RC
    def _rc(self, cena, cache, cfg, metricas, programa: rc.Programa = PROGRAMA):
        dev = cena.device
        geo = zona.geometria_rc(cena)
        irr = zona.irradiancias(cena, cache, cfg)
        da = cache.resultados.get("sol_luz.da")
        fator_luz = None if da is None else torch.nan_to_num(da.double().cpu(), nan=0.0) / 100
        z = rc.montar_zonas(geo, cena.clima, programa, irr, fator_luz, device=dev)
        h = z.u_com.shape[1]
        aq = torch.full((h,), programa.aquecimento, dtype=torch.float64, device=dev)
        rf = torch.full((h,), programa.resfriamento, dtype=torch.float64, device=dev)
        af = z.area_piso.clamp(min=1e-9)
        r = {}
        if metricas & _RC_CLIM:
            s = rc.simular(z, aq, rf, True)
            e_aq = s["phi_hc"].clamp(min=0).sum(1) / 1000      # kWh/ano
            e_rf = -s["phi_hc"].clamp(max=0).sum(1) / 1000
            luz = z.info["luz_w"].sum(1) / 1000
            equip = z.info["equip_w"].sum(1) / 1000
            consumo = e_aq / programa.cop_aquecimento + e_rf / programa.cop_resfriamento + luz + equip
            r.update(carga_aquecimento=e_aq / af, carga_resfriamento=e_rf / af,
                     carga_termica=(e_aq + e_rf) / af, eui=consumo / af, consumo=consumo)
            if "balanco_zero" in metricas:
                fv = torch.nan_to_num(cache.resultados["sol_luz.fv"].double().to(dev), nan=0.0)
                r["balanco_zero"] = fv - consumo
            if "ppd" in metricas:
                occ = rc.ocupacao(programa).bool().to(dev)
                ta = s["t_ar"][:, occ]
                tr = 2 * s["t_op"][:, occ] - ta
                rh = ci.umidade_interna(cena.clima, s["t_ar"])[:, occ]
                clo = ci.clo_horario(cena.clima).to(dev)[occ][None, :]
                _, ppd = ci.pmv_ppd(ta, tr, 0.1, rh, 1.1, clo)
                r["ppd"] = ppd.mean(1)
        if metricas & _RC_LIVRE:
            s = rc.simular(z, aq, rf, False)
            neutra = ci.neutra_ashrae55(ci.prevalente_mensal(cena.clima))
            a = ci.adaptativo(s["t_op"], neutra, rc.ocupacao(programa))
            r.update(conforto_adaptativo=a["conforto"], graus_hora_calor=a["calor"],
                     graus_hora_frio=a["frio"])
        return r

    # ------------------------------------------------------------------ geometria
    def _geometria(self, cena):
        e = [envoltoria(v) for v in cena.variantes]
        area = torch.stack([x["area"] for x in e])
        vol = torch.stack([x["volume"] for x in e])
        parede = torch.stack([x["parede"] for x in e])   # (V, 4)
        vidro = torch.stack([x["vidro"] for x in e])

        def razao(a, b):
            return torch.where(b > 0, a / b.clamp(min=1e-30) * 100, torch.zeros_like(a))

        r = {"compacidade": torch.where(vol > 0, area / vol.clamp(min=1e-30),
                                        torch.full_like(area, float("nan"))),
             "area_envoltoria": area, "volume": vol,
             "wwr": razao(vidro.sum(1), parede.sum(1)),
             "_area_piso": torch.stack([x["area_piso"] for x in e])}
        for i, o in enumerate(ORIENTACOES):
            r[f"wwr_{o}"] = razao(vidro[:, i], parede[:, i])
        return r

    # ------------------------------------------------------------------ ganho solar
    def _ganho(self, cena, cache, cfg, geo):
        n = cena.n
        r = {k: torch.zeros(n, dtype=torch.float64) for k in _GANHO}
        s = SOL_LUZ._sensores(cena, ("vidro",), cfg["grade_ext"], DESLOC_EXTERNO)
        if s is not None:
            m = SOL_LUZ._matriz(cena, cache, cfg["mf"], False)
            ceu_dir = cache.obter(("ceu_dir", id(cena.clima), cfg["mf"]), lambda: motor_a.ceu_direcional(m))
            pts, nrm, area, var = s
            rad = motor_a.radiacao_incidente(cena.raios(), pts, nrm, ceu_dir, variante=var).double().cpu()
            fs = torch.tensor([v.fator_solar for v in cena.variantes], dtype=torch.float64)[var]
            ganho = rad * fs
            r["ganho_solar"] = soma_por_variante(ganho * area.double(), var, n)
            o = orientacao(nrm.double())
            for i, nome in enumerate(ORIENTACOES):
                sel = o == i
                med = media_por_variante(ganho[sel], var[sel], n, area.double()[sel])
                r[f"ganho_solar_{nome}"] = torch.nan_to_num(med, nan=0.0)
        piso = geo.get("_area_piso") if geo else None
        if piso is None:
            piso = torch.stack([envoltoria(v)["area_piso"] for v in cena.variantes])
        r["ganho_solar_piso"] = torch.where(piso > 0, r["ganho_solar"] / piso.clamp(min=1e-30),
                                            torch.full_like(piso, float("nan")))
        return r

    # ------------------------------------------------------------------ UTCI
    def _pessoas(self, cena, grade):
        """Pessoas sobre as faces de entorno voltadas para cima (ponto a 0,92 m)."""
        pts, area, var = [], [], []
        for i, v in enumerate(cena.variantes):
            fs = cena.faces_com(i, "entorno")
            if fs.numel() == 0:
                continue
            s = sensores_de_malha(v.vertices, v.faces[fs], grade)
            cima = s.normais[:, 2] > 0.5
            p = s.pontos[cima].clone()
            p[:, 2] += ALTURA_PESSOA
            pts.append(p)
            area.append(s.areas[cima])
            var.append(torch.full((int(cima.sum()),), i))
        if not pts or sum(p.shape[0] for p in pts) == 0:
            return None
        return torch.cat(pts), torch.cat(area), torch.cat(var)

    def _utci(self, cena, cache, cfg):
        n = cena.n
        nan = torch.full((n,), float("nan"), dtype=torch.float64)
        s = self._pessoas(cena, cfg["grade_ext"])
        if s is None:
            return {k: nan.clone() for k in _UTCI}
        pts, area, var = s
        c = cena.clima
        sol, _ = SOL_LUZ._clima(cena, cache, 1)
        ceu = cache.obter(("ceu", 1), lambda: criar_ceu(1))
        coef = cache.obter(("utci_coef", id(c)), lambda: ce.utci_coeficientes(
            c["temp_bulbo_seco"], c["vento_velocidade"], c["umidade_relativa"]).float().to(cena.device))
        dia = (sol.altitude > 0).nonzero().flatten()
        ta = c["temp_bulbo_seco"]
        raios = cena.raios()
        faixas = {k: [] for k in ("conforto", "calor", "frio")}
        for i in range(0, pts.shape[0], LOTE_PESSOAS):
            sl = slice(i, i + LOTE_PESSOAS)
            ceu_exp, vis_sol = ce.exposicao(raios, pts[sl], var[sl], ceu.vetores_raio,
                                            ceu.angulo_solido, sol.vetores[dia])
            corpo = torch.zeros(ceu_exp.shape[0], ta.shape[0], device=raios.device)
            corpo[:, dia.to(raios.device)] = vis_sol.float()
            mrt = ce.mrt_externa(ta, c["rad_infravermelha_horiz"], c["dhi"], c["dni"],
                                 sol.altitude, ceu_exp, corpo)
            u = ce.utci_de_delta(coef, mrt - ta.to(mrt))
            for k, val in ce.faixas_utci(u).items():
                faixas[k].append(val.cpu())
        return {f"utci_{k}": media_por_variante(torch.cat(v), var, n, area.double())
                for k, v in faixas.items()}


TERMICO = registrar(Termico())

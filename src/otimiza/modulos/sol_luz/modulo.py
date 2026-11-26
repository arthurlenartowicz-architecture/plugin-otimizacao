"""Módulo sol_luz: motores A (radiação, sol), B (luz natural, melanópico) e C (ofuscamento)
no contrato comum. Um valor por variante para cada métrica pedida."""
from __future__ import annotations

import torch

from otimiza.modulos.sol_luz import motor_a, motor_b, motor_c
from otimiza.nucleo.ceu import criar_ceu
from otimiza.nucleo.cena import CenaLote, media_por_variante, soma_por_variante
from otimiza.nucleo.contrato import Metrica, Modulo, registrar
from otimiza.nucleo.geometria import sensores_de_malha
from otimiza.nucleo.matriz_ceu import eficacia_luminosa, matriz_ceu
from otimiza.nucleo.sol import posicao_solar_clima

NIVEIS = {
    # resolução e amostragem por nível de precisão
    # grade_olho: sensores melanópicos (4 direções); grade_vista: vistas de ofuscamento
    "rapido": dict(mf=1, grade_ext=2.0, grade_int=1.0, grade_olho=2.0, grade_vista=4.0,
                   raios=512, reflexoes=2, olho_n=32, olho_amostras=8),
    "padrao": dict(mf=1, grade_ext=1.0, grade_int=0.5, grade_olho=1.5, grade_vista=3.0,
                   raios=2048, reflexoes=4, olho_n=64, olho_amostras=16),
    "validacao": dict(mf=2, grade_ext=0.5, grade_int=0.5, grade_olho=1.0, grade_vista=1.5,
                      raios=16384, reflexoes=5, olho_n=128, olho_amostras=64),
}
DESLOC_EXTERNO = 0.1   # m, sensores sobre fachadas/cobertura
ALTURA_TRABALHO = 0.8  # m
ALTURA_OLHO = 1.2      # m

GRUPO = {
    "radiacao_fachada": "A", "radiacao_cobertura": "A", "fv": "A", "perda_sol_entorno": "A",
    "da": "B", "sda": "B", "udi": "B", "ase": "B", "df": "B",
    "medi_horas": "M", "ga": "C",
}


class SolLuz(Modulo):
    nome = "sol_luz"
    depende_de = ()
    metricas = {
        "radiacao_fachada": Metrica("kWh/m²·ano", "min", "radiação média em fachadas e vidros"),
        "radiacao_cobertura": Metrica("kWh/m²·ano", "max", "radiação média na cobertura"),
        "fv": Metrica("kWh/ano", "max", "geração FV na cobertura inteira (modelo simples)"),
        "perda_sol_entorno": Metrica("h/ano", "min", "horas de sol perdidas no entorno sensível"),
        "da": Metrica("%", "max", "DA300 médio no piso (horas ocupadas)"),
        "sda": Metrica("%", "max", "% da área com DA300 ≥ 50%"),
        "udi": Metrica("%", "max", "UDI 100–3000 lux médio"),
        "ase": Metrica("%", "min", "% da área com sol direto > 1000 lux por > 250 h"),
        "df": Metrica("%", "max", "fator de luz diurna médio (CIE encoberto)"),
        "medi_horas": Metrica("%", "max", "% das horas ocupadas com m-EDI ≥ 250 lux nos olhos"),
        "ga": Metrica("%", "max", "autonomia de ofuscamento média (DGP < 0,4)"),
    }

    # ------------------------------------------------------------------ comum
    def _clima(self, cena: CenaLote, cache, mf):
        c = cena.clima
        chave = id(c)
        sol = cache.obter(("sol", chave), lambda: posicao_solar_clima(c))
        ceu = cache.obter(("ceu", mf), lambda: criar_ceu(mf))
        return sol, ceu

    def _matriz(self, cena, cache, mf, luz):
        sol, ceu = self._clima(cena, cache, mf)
        return cache.obter(("matriz", id(cena.clima), mf, luz),
                           lambda: matriz_ceu(cena.clima, ceu, sol, luz=luz))

    def _sensores(self, cena: CenaLote, rotulos, grade, desloc=0.0, forcar_cima=False, altura=None):
        pts, nrm, area, var = [], [], [], []
        for i, v in enumerate(cena.variantes):
            fs = cena.faces_com(i, *rotulos)
            if fs.numel() == 0:
                continue
            s = sensores_de_malha(v.vertices, v.faces[fs], grade, desloc)
            p, n = s.pontos, s.normais
            if forcar_cima:  # sensores internos olhando para cima, acima do piso
                n = torch.tensor([[0, 0, 1.0]], dtype=p.dtype).expand_as(p).clone()
                p = p.clone()
                p[:, 2] = p[:, 2] + altura
            pts.append(p)
            nrm.append(n)
            area.append(s.areas)
            var.append(torch.full((s.n,), i))
        if not pts:
            return None
        return torch.cat(pts), torch.cat(nrm), torch.cat(area), torch.cat(var)

    # ------------------------------------------------------------------ avaliação
    def avaliar(self, cena: CenaLote, cache, metricas: set[str], nivel: str = "padrao"):
        cfg = NIVEIS[nivel]
        grupos = {GRUPO[m] for m in metricas}
        r = {}
        if "A" in grupos:
            r.update(self._grupo_a(cena, cache, metricas, cfg))
        if "B" in grupos:
            r.update(self._grupo_b(cena, cache, metricas, cfg))
        if "M" in grupos:
            r.update(self._grupo_m(cena, cache, cfg))
        if "C" in grupos:
            r.update(self._grupo_c(cena, cache, cfg))
        return {m: r[m] for m in metricas}

    def _grupo_a(self, cena, cache, metricas, cfg):
        n = cena.n
        dev = cena.device
        nan = torch.full((n,), float("nan"), device=dev)
        r = {k: nan.clone() for k in ("radiacao_fachada", "radiacao_cobertura", "fv", "perda_sol_entorno")}
        m = self._matriz(cena, cache, cfg["mf"], False)
        ceu_dir = cache.obter(("ceu_dir", id(cena.clima), cfg["mf"]), lambda: motor_a.ceu_direcional(m))
        raios = cena.raios()
        if metricas & {"radiacao_fachada"}:
            s = self._sensores(cena, ("fachada", "vidro"), cfg["grade_ext"], DESLOC_EXTERNO)
            if s:
                rad = motor_a.radiacao_incidente(raios, s[0], s[1], ceu_dir, variante=s[3])
                r["radiacao_fachada"] = media_por_variante(rad, s[3], n, s[2])
        if metricas & {"radiacao_cobertura", "fv"}:
            s = self._sensores(cena, ("cobertura",), cfg["grade_ext"], DESLOC_EXTERNO)
            if s:
                rad = motor_a.radiacao_incidente(raios, s[0], s[1], ceu_dir, variante=s[3])
                r["radiacao_cobertura"] = media_por_variante(rad, s[3], n, s[2])
                r["fv"] = soma_por_variante(motor_a.fotovoltaico(rad, s[2]), s[3], n)
        if "perda_sol_entorno" in metricas:
            s = self._sensores(cena, ("entorno",), cfg["grade_ext"], DESLOC_EXTERNO)
            if s:
                sol, _ = self._clima(cena, cache, cfg["mf"])
                vs = sol.vetores[sol.altitude > 0]
                perda = motor_a.perda_de_sol(cena.raios_sem_projeto(), raios, s[0], s[1], vs,
                                             variante=s[3])
                r["perda_sol_entorno"] = media_por_variante(perda, s[3], n, s[2])
        return r

    def _luz_comum(self, cena, cache, cfg):
        sol, ceu = self._clima(cena, cache, cfg["mf"])
        m = self._matriz(cena, cache, cfg["mf"], True)
        k_dir = cache.obter(("k_dir", id(cena.clima)), lambda: eficacia_luminosa(cena.clima, sol)[1])
        horas = ((sol.altitude > 0) & (cena.clima["dni"] > 0)).nonzero().flatten()
        return sol, ceu, m, cena.clima["dni"] * k_dir, horas

    def _grupo_b(self, cena, cache, metricas, cfg):
        n = cena.n
        sol, ceu, m, e_sol, horas = self._luz_comum(cena, cache, cfg)
        s = self._sensores(cena, ("piso",), cfg["grade_int"], forcar_cima=True, altura=ALTURA_TRABALHO)
        dev = cena.device
        if s is None:
            return {k: torch.full((n,), float("nan"), device=dev) for k in ("da", "sda", "udi", "ase", "df")}
        p, nrm, area, var = s
        raios, mat = cena.raios(), cena.materiais()
        coef = motor_b.coeficientes_luz(raios, mat, ceu, p, nrm, variante=var,
                                        n_raios=cfg["raios"], max_reflexoes=cfg["reflexoes"])
        fs = motor_b.fator_sol_direto(raios, mat, p, nrm, sol.vetores[horas], variante=var)
        occ = motor_b.horas_ocupadas(cena.clima)
        r = {}
        if metricas & {"da", "sda", "udi"}:
            e = motor_b.iluminancia_anual(coef, m, fs, e_sol, horas)
            met = motor_b.metricas_anuais(e, occ)
            r["da"] = media_por_variante(met["da"], var, n, area)
            r["sda"] = media_por_variante((met["da"] >= 50).float() * 100, var, n, area)
            r["udi"] = media_por_variante(met["udi"], var, n, area)
        if "ase" in metricas:
            e_sol_dir = motor_b.iluminancia_anual(coef, m, fs, e_sol, horas, so_sol_direto=True)
            horas_acima = (e_sol_dir[:, occ.to(e_sol_dir.device)] > 1000).sum(1)
            r["ase"] = media_por_variante((horas_acima > 250).float() * 100, var, n, area)
        if "df" in metricas:
            r["df"] = media_por_variante(motor_b.fator_luz_diurna(coef, ceu), var, n, area)
        return r

    def _olhos(self, cena, cfg, chave_grade="grade_olho"):
        s = self._sensores(cena, ("piso",), cfg[chave_grade], forcar_cima=True, altura=ALTURA_OLHO)
        if s is None:
            return None
        p, _, _, var = s
        pts, nrm, orig = motor_b.sensores_olho(p, altura=ALTURA_OLHO)
        pts[:, 2] = p[orig, 2]  # mantém a altura absoluta (piso + 1,2 m)
        return pts, nrm, var[orig]

    def _grupo_m(self, cena, cache, cfg):
        n = cena.n
        sol, ceu, m, e_sol, horas = self._luz_comum(cena, cache, cfg)
        o = self._olhos(cena, cfg)
        if o is None:
            return {"medi_horas": torch.full((n,), float("nan"), device=cena.device)}
        pts, nrm, var = o
        raios, mat = cena.raios(), cena.materiais().melanopico()
        coef = motor_b.coeficientes_luz(raios, mat, ceu, pts, nrm, variante=var,
                                        n_raios=cfg["raios"], max_reflexoes=cfg["reflexoes"])
        fs = motor_b.fator_sol_direto(raios, mat, pts, nrm, sol.vetores[horas], variante=var)
        medi = motor_b.iluminancia_melanopica_anual(coef, m, fs, e_sol, horas)
        h = motor_b.metricas_circadianas(medi, cena.clima)["horas_pct"]
        return {"medi_horas": media_por_variante(h, var, n)}

    def _grupo_c(self, cena, cache, cfg, lote_vistas: int = 64):
        n = cena.n
        sol, ceu, m, e_sol, _ = self._luz_comum(cena, cache, cfg)
        o = self._olhos(cena, cfg, "grade_vista")
        if o is None:
            return {"ga": torch.full((n,), float("nan"), device=cena.device)}
        pts, frente, var = o
        raios, mat = cena.raios(), cena.materiais()
        occ = motor_b.horas_ocupadas(cena.clima).nonzero().flatten()
        ga = []
        for i in range(0, pts.shape[0], lote_vistas):
            sl = slice(i, i + lote_vistas)
            v = motor_c.coeficientes_vistas(raios, mat, ceu, pts[sl], frente[sl], variante=var[sl],
                                            n=cfg["olho_n"], amostras=cfg["olho_amostras"],
                                            max_reflexoes=cfg["reflexoes"])
            _, g = motor_c.dgp_anual(raios, mat, v, m, sol.vetores, e_sol, occ)
            ga.append(motor_c.autonomia_ofuscamento(g))
        return {"ga": media_por_variante(torch.cat(ga), var, n)}


SOL_LUZ = registrar(SolLuz())

"""Da CenaLote para as entradas do modelo RC: áreas por tipo e radiação horária (motor A)."""
from __future__ import annotations

import torch

from otimiza.modulos.sol_luz import motor_a
from otimiza.modulos.sol_luz.modulo import DESLOC_EXTERNO, SOL_LUZ
from otimiza.modulos.termico.envoltoria import envoltoria
from otimiza.nucleo.cena import CODIGO
from otimiza.nucleo.geometria import normais_e_areas

TIPOS = ("parede", "cobertura", "vidro")


def geometria_rc(cena, piso_base: str = "solo") -> dict[str, torch.Tensor]:
    """(V,) áreas em m². Piso na cota da base = no solo (piso_base="solo") ou adiabático
    ("adiabatico", ex.: pavimento sobre outro climatizado); pisos acima = lajes internas
    (massa dos dois lados, área contada 2×)."""
    out = {k: [] for k in ("parede", "vidro", "cobertura", "solo", "piso_interno", "area_piso",
                           "volume", "fs", "altura_parede", "altura_cobertura")}
    for v in cena.variantes:
        vert = v.vertices.double()
        e = envoltoria(v)

        def area(rot):
            f = v.faces[v.rotulos == CODIGO[rot]]
            return normais_e_areas(vert, f)[1].sum() if f.numel() else torch.tensor(0.0, dtype=torch.float64)

        def altura(*rots):  # altura média (ponderada pela área) acima da base do prédio
            m = torch.zeros_like(v.rotulos, dtype=torch.bool)
            for r in rots:
                m |= v.rotulos == CODIGO[r]
            f = v.faces[m]
            if not f.numel():
                return torch.tensor(1.0, dtype=torch.float64)
            a = normais_e_areas(vert, f)[1]
            return (vert[f, 2].mean(1) * a).sum() / a.sum().clamp(min=1e-30) - z0

        fp = v.faces[v.rotulos == CODIGO["piso"]]
        env = v.faces[(v.rotulos == CODIGO["fachada"]) | (v.rotulos == CODIGO["vidro"])
                      | (v.rotulos == CODIGO["cobertura"])]
        z0 = vert[env.flatten(), 2].min() if env.numel() else torch.tensor(0.0, dtype=torch.float64)
        base = interno = torch.tensor(0.0, dtype=torch.float64)
        if fp.numel():
            env = v.faces[(v.rotulos == CODIGO["fachada"]) | (v.rotulos == CODIGO["vidro"])
                          | (v.rotulos == CODIGO["cobertura"])]
            z_base = vert[env.flatten(), 2].min() if env.numel() else vert[fp.flatten(), 2].min()
            a = normais_e_areas(vert, fp)[1]
            na_base = (vert[fp, 2].mean(1) - z_base).abs() < 0.05
            base, interno = a[na_base].sum(), 2 * a[~na_base].sum()
        out["altura_parede"].append(altura("fachada", "vidro"))
        out["altura_cobertura"].append(altura("cobertura"))
        out["parede"].append(area("fachada"))
        out["vidro"].append(area("vidro"))
        out["cobertura"].append(area("cobertura"))
        out["solo"].append(base if piso_base == "solo" else torch.tensor(0.0, dtype=torch.float64))
        out["piso_interno"].append(interno + (base if piso_base != "solo" else 0.0))
        out["area_piso"].append(e["area_piso"])
        out["volume"].append(e["volume"])
        out["fs"].append(torch.tensor(float(v.fator_solar), dtype=torch.float64))
    return {k: torch.stack(v) for k, v in out.items()}


def irradiancias(cena, cache, cfg) -> dict[str, torch.Tensor]:
    """(V, H) W: Σ área · irradiância nas paredes opacas, coberturas e vidros (vidros já com
    o fator angular do vidro duplo). Céu horário do mesmo nível do sol_luz."""
    n = cena.n
    m = SOL_LUZ._matriz(cena, cache, cfg["mf"], False)
    ceu = cache.obter(("ceu_horario", id(cena.clima), cfg["mf"]),
                      lambda: motor_a.ceu_direcional_horario(m))
    pts, nrm, area, grupo, ang = [], [], [], [], []
    var_todas = []
    for t, rot in enumerate(TIPOS):
        s = SOL_LUZ._sensores(cena, ("fachada",) if rot == "parede" else (rot,),
                              cfg["grade_ext"], DESLOC_EXTERNO)
        if s is None:
            continue
        pts.append(s[0]); nrm.append(s[1]); area.append(s[2])
        grupo.append(s[3] * len(TIPOS) + t)
        ang.append(torch.full((s[0].shape[0],), rot == "vidro"))
        var_todas.append(s[3])
    h = m.total.shape[1]
    if not pts:
        return {k: torch.zeros(n, h, dtype=torch.float64) for k in TIPOS}
    irr = motor_a.irradiancia_agregada(cena.raios(), torch.cat(pts), torch.cat(nrm), torch.cat(area),
                                       torch.cat(grupo), n * len(TIPOS), ceu,
                                       variante=torch.cat(var_todas), fator_angular=torch.cat(ang))
    irr = irr.reshape(n, len(TIPOS), h)
    saida = {k: irr[:, i] for i, k in enumerate(TIPOS)}
    # parte direta (sol) nos vidros: vai para o piso; o difuso se espalha
    sel = torch.cat(ang)
    if sel.any():
        ceu_dir = cache.obter(("ceu_horario_dir", id(cena.clima), cfg["mf"]),
                              lambda: motor_a.ceu_direcional_horario(m, componente="direta"))
        var_v = torch.cat(var_todas)[sel]
        saida["vidro_direta"] = motor_a.irradiancia_agregada(
            cena.raios(), torch.cat(pts)[sel], torch.cat(nrm)[sel], torch.cat(area)[sel], var_v, n,
            ceu_dir, variante=var_v, fator_angular=torch.ones(int(sel.sum()), dtype=torch.bool))
    else:
        saida["vidro_direta"] = torch.zeros(n, h, dtype=torch.float64)
    return saida

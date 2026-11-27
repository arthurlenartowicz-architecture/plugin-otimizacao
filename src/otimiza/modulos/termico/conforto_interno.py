"""Conforto interno em lote: adaptativo da ASHRAE 55 (evolução livre) e PMV/PPD de Fanger.

Adaptativo (ASHRAE 55, como o padrão do Ladybug): temperatura prevalente = média mensal do
ar externo, limitada a 10–33,5 °C; neutra = 0,31·T_prev + 17,8; confortável se
|T_op − neutra| ≤ 3,5 °C (80% de aceitação). Graus-hora = soma do quanto a operativa passa
da faixa (acima: calor; abaixo: frio).
PMV/PPD: Fanger (ISO 7730), a mesma iteração do ladybug_comfort, em lote; roupa pelo modelo
de Schiavon & Lee (2013) com a temperatura externa das 6 h do dia.
"""
from __future__ import annotations

import torch

FAIXA_ASHRAE_80 = 3.5


def prevalente_mensal(clima) -> torch.Tensor:
    """(H,) média mensal do ar externo do mês de cada hora."""
    t = clima["temp_bulbo_seco"].double()
    mes = (clima.mes - 1).long()
    soma = torch.zeros(12, dtype=torch.float64).index_add_(0, mes, t)
    n = torch.zeros(12, dtype=torch.float64).index_add_(0, mes, torch.ones_like(t))
    return (soma / n)[mes]


def neutra_ashrae55(prevalente: torch.Tensor) -> torch.Tensor:
    return 0.31 * prevalente.double().clamp(10.0, 33.5) + 17.8


def adaptativo(t_op: torch.Tensor, neutra: torch.Tensor, ocupadas: torch.Tensor,
               faixa: float = FAIXA_ASHRAE_80) -> dict[str, torch.Tensor]:
    """t_op (V, H); neutra e ocupadas (H,). % das horas ocupadas na faixa e graus-hora."""
    d = t_op - neutra.to(t_op)[None, :]
    occ = ocupadas.to(t_op).bool()
    d = d[:, occ]
    return {"conforto": ((d.abs() <= faixa).double().mean(1) * 100),
            "calor": (d - faixa).clamp(min=0).sum(1),
            "frio": (-faixa - d).clamp(min=0).sum(1)}


def schiavon_clo(t6: torch.Tensor, max_clo=1.0, max_clo_temp=-5.0, min_clo=0.46,
                 min_clo_temp=26.0) -> torch.Tensor:
    """Roupa (clo) pela temperatura externa das 6 h (Schiavon & Lee 2013)."""
    t = t6.double()
    meio = max_clo - (max_clo - min_clo) * 0.75
    f1 = (meio - max_clo) / 10 * (t - max_clo_temp) + max_clo
    f2 = (min_clo - meio) / (min_clo_temp - (max_clo_temp + 10)) * (t - min_clo_temp) + min_clo
    return torch.where(t <= max_clo_temp, torch.full_like(t, max_clo),
                       torch.where(t < max_clo_temp + 10, f1,
                                   torch.where(t < min_clo_temp, f2, torch.full_like(t, min_clo))))


def clo_horario(clima) -> torch.Tensor:
    """(H,) clo de cada hora pela temperatura externa das 6 h do mesmo dia."""
    t = clima["temp_bulbo_seco"].double().reshape(-1, 24)
    t6 = t[:, 6]   # linha da hora 7 do EPW = intervalo 6–7 h
    return schiavon_clo(t6).repeat_interleave(24)


def pmv_ppd(ta, tr, vel, rh, met, clo, wme=0.0, iteracoes: int = 150):
    """PMV e PPD (%) de Fanger, elemento a elemento (tensores com a mesma forma ou escalares)."""
    args = (ta, tr, vel, rh, met, clo)
    dev = next((x.device for x in args if isinstance(x, torch.Tensor)), torch.device("cpu"))
    ta, tr, vel, rh, met, clo = torch.broadcast_tensors(
        *[torch.as_tensor(x, dtype=torch.float64).to(dev) for x in args])
    pa = rh * 10.0 * torch.exp(16.6536 - 4030.183 / (ta + 235.0))
    icl = 0.155 * clo
    m = met * 58.15
    mw = m - wme * 58.15
    fcl = torch.where(icl <= 0.078, 1 + 1.29 * icl, 1.05 + 0.645 * icl)
    hcf = 12.1 * torch.sqrt(vel)
    taa, tra = ta + 273.0, tr + 273.0
    tcla = taa + (35.5 - ta) / (3.5 * icl + 0.1)
    p1 = icl * fcl
    p2, p3, p4 = p1 * 3.96, p1 * 100.0, p1 * taa
    p5 = 308.7 - 0.028 * mw + p2 * (tra / 100.0) ** 4
    xn, xf = tcla / 100.0, tcla / 50.0
    for _ in range(iteracoes):          # mesma iteração do ladybug (média + ponto fixo)
        ativo = (xn - xf).abs() > 0.00015
        if not bool(ativo.any()):
            break
        xf2 = (xf + xn) / 2.0
        hc = torch.maximum(hcf, 2.38 * (100.0 * xf2 - taa).abs() ** 0.25)
        xn2 = (p5 + p4 * hc - p2 * xf2 ** 4) / (100.0 + p3 * hc)
        xf = torch.where(ativo, xf2, xf)
        xn = torch.where(ativo, xn2, xn)
    hc = torch.maximum(hcf, 2.38 * (100.0 * xf - taa).abs() ** 0.25)
    tcl = 100.0 * xn - 273.0
    hl1 = 3.05e-3 * (5733.0 - 6.99 * mw - pa)
    hl2 = torch.where(mw > 58.15, 0.42 * (mw - 58.15), torch.zeros_like(mw))
    hl3 = 1.7e-5 * m * (5867.0 - pa)
    hl4 = 0.0014 * m * (34.0 - ta)
    hl5 = 3.96 * fcl * (xn ** 4 - (tra / 100.0) ** 4)
    hl6 = fcl * hc * (tcl - ta)
    ts = 0.303 * torch.exp(-0.036 * m) + 0.028
    pmv = ts * (mw - hl1 - hl2 - hl3 - hl4 - hl5 - hl6)
    ppd = 100.0 - 95.0 * torch.exp(-0.03353 * pmv ** 4 - 0.2179 * pmv ** 2)
    return pmv, ppd


def umidade_interna(clima, t_ar: torch.Tensor) -> torch.Tensor:
    """(V, H) umidade relativa interna (%) com a mesma pressão de vapor do ar externo
    (sem fontes de umidade), pela fórmula de saturação do PMV."""
    te = clima["temp_bulbo_seco"].double().to(t_ar)
    rh_e = clima["umidade_relativa"].double().to(t_ar)
    psat = lambda t: torch.exp(16.6536 - 4030.183 / (t + 235.0))  # noqa: E731
    return (rh_e * psat(te))[None, :] / psat(t_ar)

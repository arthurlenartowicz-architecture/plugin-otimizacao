"""4b: modelo RC multicamadas contra o EnergyPlus (honeybee_energy) e conforto interno contra
o ladybug_comfort. Gabarito: scripts/gerar_ref_rc.py → testes/referencias/rc/eplus_rc.json."""
import pytest
import torch

import otimiza.modulos.termico  # noqa: F401  (registra o módulo)
from otimiza.modulos.sol_luz.modulo import NIVEIS
from otimiza.modulos.termico import conforto_interno as ci
from otimiza.modulos.termico import rc, zona
from otimiza.nucleo.cache import Cache
from otimiza.nucleo.cena import CenaLote
from otimiza.nucleo.clima import ler_epw
from otimiza.nucleo.motor import avaliar
from otimiza.nucleo.prototipos import predio
from testes.conftest import PASTA_EPW
from testes.referencias import PASTA, carregar

REF = PASTA / "rc" / "eplus_rc.json"
EPW = PASTA_EPW / "BRA_PR_Curitiba.838420_INMET.epw"
pytestmark = pytest.mark.skipif(not (REF.is_file() and EPW.is_file()), reason="sem gabarito/EPW")
CASOS = {"A": ({"N": 0.4}, "adiabatico"), "B": ({"N": 0.4, "O": 0.3}, "solo")}


@pytest.fixture(scope="module")
def clima():
    return ler_epw(EPW)


@pytest.fixture(scope="module")
def ref():
    r = carregar(PASTA / "rc", "eplus_rc.json")
    return {"geral": r, **{c["nome"]: c for c in r["casos"]}}


@pytest.fixture(scope="module")
def simulados(clima):
    """Nossa simulação dos casos A e B (climatizado e livre), nível padrão."""
    out = {}
    p = rc.Programa()
    for nome, (jan, piso) in CASOS.items():
        cena = CenaLote([predio(janelas=jan)], clima)
        z = rc.montar_zonas(zona.geometria_rc(cena, piso), clima, p,
                            zona.irradiancias(cena, Cache(), NIVEIS["padrao"]), device=cena.device)
        aq = torch.full((8760,), p.aquecimento, dtype=torch.float64, device=cena.device)
        rf = torch.full((8760,), p.resfriamento, dtype=torch.float64, device=cena.device)
        out[nome] = {k: v[0].cpu() for k, v in rc.simular(z, aq, rf, True).items()}
        out[nome + "_livre"] = {k: v[0].cpu() for k, v in rc.simular(z, aq, rf, False).items()}
    return out


def test_mesmo_programa_do_gabarito(ref):
    r = ref["geral"]
    assert rc.ocupacao(rc.Programa()).tolist() == r["ocupacao"]
    p = rc.Programa()
    c = r["cargas"]
    assert (p.pessoas_m2, p.iluminacao_w_m2, p.equipamentos_w_m2, p.ach, p.aquecimento, p.resfriamento) == \
        (c["pessoas_m2"], c["iluminacao_w_m2"], c["equipamentos_w_m2"], c["ach"], c["aquecimento"],
         c["resfriamento"])


@pytest.mark.parametrize("nome", list(CASOS))
def test_cargas_contra_energyplus(simulados, ref, nome, capsys):
    phi = simulados[nome]["phi_hc"]
    aq, rf = phi.clamp(min=0).sum().item() / 1000, -phi.clamp(max=0).sum().item() / 1000
    e = ref[nome]["eplus"]
    e_aq, e_rf = sum(e["aquecimento_kwh"]), sum(e["resfriamento_kwh"])
    with capsys.disabled():
        print(f"\n{nome}: aquecimento {aq:.0f} × E+ {e_aq:.0f} ({aq / e_aq - 1:+.1%}); "
              f"resfriamento {rf:.0f} × {e_rf:.0f} ({rf / e_rf - 1:+.1%}); "
              f"total {(aq + rf) / (e_aq + e_rf) - 1:+.1%}")
    # aquecimento é pequeno em Curitiba (~10% da carga): erro relativo maior, ≤ 3% do total
    assert aq == pytest.approx(e_aq, rel=0.25)
    assert abs(aq - e_aq) < 0.03 * (e_aq + e_rf)
    assert rf == pytest.approx(e_rf, rel=0.10)
    assert aq + rf == pytest.approx(e_aq + e_rf, rel=0.10)
    # mês a mês o resfriamento acompanha (correlação dos totais mensais)
    mes = torch.arange(8760) // 730
    nos_m = torch.zeros(12).index_add_(0, mes, (-phi.clamp(max=0)).float())
    ep_m = torch.zeros(12).index_add_(0, mes, torch.tensor(e["resfriamento_kwh"]))
    assert torch.corrcoef(torch.stack([nos_m, ep_m]))[0, 1] > 0.97


@pytest.mark.parametrize("nome", list(CASOS))
def test_evolucao_livre_contra_energyplus(simulados, ref, nome, capsys):
    top = simulados[nome + "_livre"]["t_op"]
    e = torch.tensor(ref[nome + "_livre"]["eplus"]["temp_operativa"], dtype=torch.float64)
    d = top - e
    amp = lambda t: (t.reshape(365, 24).amax(1) - t.reshape(365, 24).amin(1)).mean().item()  # noqa: E731
    with capsys.disabled():
        print(f"\n{nome} livre: T_op média {top.mean():.2f} × E+ {e.mean():.2f}, rms {d.pow(2).mean().sqrt():.2f}, "
              f"amplitude diária {amp(top):.2f} × {amp(e):.2f}")
    assert d.mean().abs().item() < 0.4
    assert d.pow(2).mean().sqrt().item() < 0.8
    assert amp(top) == pytest.approx(amp(e), abs=1.0)


def test_adaptativo_contra_ladybug(clima, ref):
    """Mesma operativa (do E+): hora a hora o mesmo 'confortável' do LB (ASHRAE 55, 80%)."""
    for nome in ("A_livre", "B_livre"):
        lb = ref[nome]["ladybug_adaptativo"]
        top = torch.tensor(ref[nome]["eplus"]["temp_operativa"], dtype=torch.float64)[None, :]
        neutra = ci.neutra_ashrae55(torch.tensor(lb["prevalente"], dtype=torch.float64))
        assert (neutra - torch.tensor(lb["neutra"])).abs().max() < 1e-4
        todas = torch.ones(8760)
        nosso = ((top - neutra).abs() <= ci.FAIXA_ASHRAE_80)[0]
        assert (nosso == torch.tensor(lb["confortavel"]).bool()).float().mean() > 0.9995
        assert ci.adaptativo(top, neutra, todas)["conforto"].item() == \
            pytest.approx(100 * sum(lb["confortavel"]) / 8760, abs=0.05)
        # a nossa prevalente (média mensal do EPW sem o deslocamento do LB) quase igual
        assert (ci.prevalente_mensal(clima) - torch.tensor(lb["prevalente"])).abs().max() < 0.05


@pytest.mark.parametrize("nome", list(CASOS))
def test_conforto_adaptativo_rc_vs_energyplus(clima, simulados, ref, nome):
    neutra = ci.neutra_ashrae55(ci.prevalente_mensal(clima))
    occ = rc.ocupacao(rc.Programa())
    nos = ci.adaptativo(simulados[nome + "_livre"]["t_op"][None, :], neutra, occ)
    ep = ci.adaptativo(torch.tensor(ref[nome + "_livre"]["eplus"]["temp_operativa"],
                                    dtype=torch.float64)[None, :], neutra, occ)
    print(f"\n{nome}: conforto {nos['conforto'].item():.1f}% × E+ {ep['conforto'].item():.1f}%; "
          f"GH calor {nos['calor'].item():.0f} × {ep['calor'].item():.0f}")
    assert nos["conforto"].item() == pytest.approx(ep["conforto"].item(), abs=5.0)


def test_pmv_contra_ladybug(ref):
    p = torch.tensor(ref["geral"]["pmv_pontos"], dtype=torch.float64)
    ta, tr, vel, rh, met, t6, clo, pmv_lb, ppd_lb = p.T
    assert (ci.schiavon_clo(t6) - clo).abs().max() < 1e-12
    pmv, ppd = ci.pmv_ppd(ta, tr, vel, rh, met, clo)
    assert (pmv - pmv_lb).abs().max() < 1e-6
    assert (ppd - ppd_lb).abs().max() < 1e-4


def test_sem_ganhos_fica_na_temperatura_externa(clima):
    """Tudo a 10 °C, sem sol nem ganhos: a zona fica a 10 °C; com termostato 20, a potência
    é a de regime e o ar fica em 20."""
    cena = CenaLote([predio()], clima)
    geo = zona.geometria_rc(cena)
    zero = {k: torch.zeros(1, 8760, dtype=torch.float64) for k in zona.TIPOS}
    p = rc.Programa(pessoas_m2=0, iluminacao_w_m2=0, equipamentos_w_m2=0)
    z = rc.montar_zonas(geo, clima, p, zero, device=cena.device)
    z.u_com[0] = 10.0
    z.u_com[1] = 10.0
    z.u_com[2] = 0.0
    dev = z.lam.device
    livre = rc.simular(z, torch.full((8760,), -99.0, dtype=torch.float64, device=dev),
                       torch.full((8760,), 99.0, dtype=torch.float64, device=dev), True, passadas=3)
    assert (livre["t_ar"][0, -24:] - 10).abs().max() < 1e-6
    assert livre["phi_hc"].abs().max() == 0
    clim = rc.simular(z, torch.full((8760,), 20.0, dtype=torch.float64, device=dev),
                      torch.full((8760,), 24.0, dtype=torch.float64, device=dev), True, passadas=3)
    assert (clim["t_ar"][0, -24:] - 20).abs().max() < 1e-9
    pot = clim["phi_hc"][0, -24:]
    assert (pot > 0).all() and (pot.max() - pot.min()) < 1e-6 * pot.max()


def test_motor_so_roda_o_necessario(clima):
    cena = CenaLote([predio(), predio(janelas={"N": 0.6})], clima)
    cache = Cache()
    avaliar(cena, ["termico.compacidade"], "rapido", cache)
    assert not any(k.startswith("sol_luz.") for k in cache.resultados)
    r = avaliar(cena, ["termico.eui", "termico.balanco_zero", "termico.consumo"], "rapido", cache)
    assert {k for k in cache.resultados if k.startswith("sol_luz.")} == {"sol_luz.da", "sol_luz.fv"}
    fv = cache.resultados["sol_luz.fv"]
    assert torch.allclose(r["termico.balanco_zero"], fv.double().to(r["termico.consumo"]) - r["termico.consumo"])
    # mais vidro ao norte: mais luz natural (menos iluminação), mais ganho solar
    assert r["termico.eui"].shape == (2,)


def test_metricas_rc_no_modulo(clima):
    cena = CenaLote([predio(janelas={"N": 0.2}), predio(janelas={"N": 0.7})], clima)
    ch = ["termico." + m for m in ("carga_aquecimento", "carga_resfriamento", "carga_termica", "ppd",
                                   "conforto_adaptativo", "graus_hora_calor", "graus_hora_frio")]
    r = {k.split(".")[1]: v.cpu() for k, v in avaliar(cena, ch, "rapido").items()}
    assert torch.allclose(r["carga_termica"], r["carga_aquecimento"] + r["carga_resfriamento"])
    assert r["carga_resfriamento"][1] > r["carga_resfriamento"][0]   # mais vidro ao norte, mais sol
    assert r["graus_hora_calor"][1] > r["graus_hora_calor"][0]
    assert ((r["ppd"] >= 5) & (r["ppd"] < 100)).all()
    assert ((r["conforto_adaptativo"] >= 0) & (r["conforto_adaptativo"] <= 100)).all()

"""4a: módulo termico (🟢) — envoltória/WWR analíticos, ganho solar contra o EPW,
UTCI e MRT contra o ladybug_comfort (gabarito de scripts/gerar_ref_termico.py)."""
import math

import pytest
import torch

import otimiza.modulos.termico  # noqa: F401  (registra o módulo)
from otimiza.modulos.termico import conforto_externo as ce
from otimiza.modulos.termico.envoltoria import envoltoria
from otimiza.nucleo.cena import CODIGO, CenaLote, Variante
from otimiza.nucleo.ceu import criar_ceu
from otimiza.nucleo.clima import ler_epw
from otimiza.nucleo.geometria import caixa, juntar, retangulo
from otimiza.nucleo.motor import avaliar
from otimiza.nucleo.prototipos import predio
from otimiza.nucleo.raios import Cena
from otimiza.nucleo.sol import posicao_solar_clima
from testes.conftest import PASTA_EPW
from testes.referencias import PASTA, carregar

REF = PASTA / "curitiba" / "termico_ladybug.json"
EPW = PASTA_EPW / "BRA_PR_Curitiba.838420_INMET.epw"
precisa_ref = pytest.mark.skipif(not (REF.is_file() and EPW.is_file()), reason="sem gabarito/EPW")


@pytest.fixture(scope="module")
def clima():
    return ler_epw(EPW)


@pytest.fixture(scope="module")
def ref():
    return carregar(PASTA / "curitiba", "termico_ladybug.json")


def variante_de(malha, rotulo="cobertura", **kw):
    v, f = malha
    n = f.shape[0]
    rot = torch.full((n,), CODIGO[rotulo])
    tau = torch.full((n,), 0.64 if rotulo == "vidro" else 0.0)
    return Variante(v, f, rot, torch.full((n,), 0.3), tau, **kw)


# ---------------------------------------------------------------- envoltória e WWR
def test_envoltoria_caixa():
    """Prédio 6×8×3 (piso rotulado piso, não entra): A = 180 m², V = 144 m³."""
    for rot in (0.0, 37.0):
        e = envoltoria(predio(rotacao_graus=rot))
        assert e["area"].item() == pytest.approx(180.0, rel=1e-9)
        assert e["volume"].item() == pytest.approx(144.0, rel=1e-9)
        assert e["area_piso"].item() == pytest.approx(48.0, rel=1e-9)
    # caixa fechada (base na própria envoltória) e elevada: não conta a base duas vezes
    e = envoltoria(variante_de(caixa(1, 2, 5, 4, 6, 7.5), "fachada"))
    assert e["area"].item() == pytest.approx(2 * (12 + 7.5 + 10), rel=1e-9)
    assert e["volume"].item() == pytest.approx(30.0, rel=1e-9)


def test_compacidade_e_wwr_no_modulo():
    cena = CenaLote([predio(janelas={"N": 0.4, "L": 0.25}),
                     predio(janelas={"N": 0.4, "L": 0.25}, rotacao_graus=90),
                     predio(largura=12, profundidade=12, altura=6, janelas={})], None, device="cpu")
    chaves = ["termico." + m for m in ("compacidade", "wwr", "wwr_N", "wwr_L", "wwr_S", "wwr_O")]
    r = {k.split(".")[1]: v.cpu() for k, v in avaliar(cena, chaves).items()}
    assert r["compacidade"].tolist() == pytest.approx([1.25, 1.25, (4 * 72 + 2 * 144) / 864])
    assert r["wwr_N"].tolist() == pytest.approx([40, 25, 0])   # girado 90°: leste → norte
    assert r["wwr_L"].tolist() == pytest.approx([25, 0, 0])
    assert r["wwr_O"].tolist() == pytest.approx([0, 40, 0])    # norte → oeste
    assert r["wwr_S"].tolist() == pytest.approx([0, 0, 0])
    assert r["wwr"][0].item() == pytest.approx((0.4 * 18 + 0.25 * 24) / 84 * 100)


# ---------------------------------------------------------------- ganho solar
@precisa_ref
def test_ganho_solar_claraboia_contra_epw(clima):
    """Vidro horizontal sem obstrução: ganho = A · FS · Σ (DHI + DNI · sen alt) do EPW."""
    sol = posicao_solar_clima(clima)
    gh = (clima["dhi"] + clima["dni"] * torch.sin(torch.deg2rad(sol.altitude)).clamp(min=0))
    gh = torch.where(sol.altitude > 0, gh, torch.zeros_like(gh)).sum().item() / 1000  # kWh/m²
    v = variante_de(retangulo(0, 0, 4, 3, z=0.0), "vidro", fator_solar=0.5)
    for nivel in ("rapido", "validacao"):
        g = avaliar(CenaLote([v], clima), ["termico.ganho_solar"], nivel)["termico.ganho_solar"].item()
        print(f"\nclaraboia {nivel}: {g:.1f} kWh × esperado {12 * 0.5 * gh:.1f}")
        assert g == pytest.approx(12 * 0.5 * gh, rel=0.01)


@precisa_ref
def test_ganho_solar_por_orientacao(clima):
    """Total = Σ por orientação × área de vidro; girar o prédio muda o total, não o ganho por m²."""
    a = predio(janelas={"N": 0.4, "L": 0.3, "S": 0.2, "O": 0.5})
    b = predio(janelas={"N": 0.4, "L": 0.3, "S": 0.2, "O": 0.5}, rotacao_graus=90)
    cena = CenaLote([a, b], clima)
    ch = ["termico.ganho_solar"] + [f"termico.ganho_solar_{o}" for o in "NLSO"]
    r = {k.split(".")[1]: v.cpu() for k, v in avaliar(cena, ch, "padrao").items()}
    vid = envoltoria(a)["vidro"]
    soma = sum(r[f"ganho_solar_{o}"][0] * vid[i] for i, o in enumerate("NLSO"))
    assert soma.item() == pytest.approx(r["ganho_solar"][0].item(), rel=1e-6)
    # hemisfério sul: norte recebe mais que sul
    assert r["ganho_solar_N"][0] > r["ganho_solar_S"][0]
    # sem obstrução o ganho por m² só depende da orientação (o céu não gira com o prédio),
    # mas o total muda porque outra janela passa a olhar para o norte
    for o in "NLSO":
        assert r[f"ganho_solar_{o}"][1].item() == pytest.approx(r[f"ganho_solar_{o}"][0].item(), rel=0.02)
    assert r["ganho_solar"][1] != pytest.approx(r["ganho_solar"][0].item(), rel=0.02)


# ---------------------------------------------------------------- UTCI e MRT
@precisa_ref
def test_utci_pontos_contra_ladybug(ref):
    p = torch.tensor(ref["utci_pontos"], dtype=torch.float64)
    u = ce.utci(p[:, 0], p[:, 1], p[:, 2], p[:, 3])
    assert (u - p[:, 4]).abs().max().item() < 1e-9


@precisa_ref
@pytest.mark.parametrize("i", [0, 1, 2])
def test_mrt_e_utci_horarios_contra_ladybug(ref, i):
    """Mesmas entradas e altitudes do LB (OutdoorSolarCal + UTCI): MRT e UTCI hora a hora."""
    caso = ref["casos"][i]
    e = {k: torch.tensor(v, dtype=torch.float64) for k, v in ref["entradas"].items()}
    alt = torch.tensor(caso["altitude"], dtype=torch.float64)
    corpo = torch.full((1, 8760), caso["fract_exposed"], dtype=torch.float64)
    mrt = ce.mrt_externa(e["ta"], e["ir"], e["dhi"], e["dni"], alt,
                         torch.tensor([caso["sky_exposure"]]), corpo)[0]
    assert (mrt - torch.tensor(caso["mrt"])).abs().max().item() < 1e-4
    coef = ce.utci_coeficientes(e["ta"], e["vento"], e["rh"])
    u = ce.utci_de_delta(coef, mrt - e["ta"])
    assert (u - torch.tensor(caso["utci"])).abs().max().item() < 1e-3
    f = ce.faixas_utci(u)
    assert f["conforto"].item() == pytest.approx(caso["conforto_pct"], abs=0.02)
    assert f["calor"].item() == pytest.approx(caso["calor_pct"], abs=0.02)
    assert f["frio"].item() == pytest.approx(caso["frio_pct"], abs=0.02)


@precisa_ref
def test_utci_campo_aberto_no_modulo(clima, ref, capsys):
    """Entorno plano sem nada em volta = caso campo aberto do LB. Diferença só pelo sol no
    meio da hora (nós) × hora cheia (LB)."""
    piso = variante_de(retangulo(-10, -10, 10, 10), "entorno")
    r = avaliar(CenaLote([piso], clima), ["termico.utci_conforto", "termico.utci_calor",
                                          "termico.utci_frio"], "rapido")
    lb = ref["casos"][0]
    with capsys.disabled():
        print(f"\ncampo aberto: conforto {r['termico.utci_conforto'].item():.2f} × LB "
              f"{lb['conforto_pct']:.2f}; calor {r['termico.utci_calor'].item():.2f} × "
              f"{lb['calor_pct']:.2f}; frio {r['termico.utci_frio'].item():.2f} × {lb['frio_pct']:.2f}")
    assert r["termico.utci_conforto"].item() == pytest.approx(lb["conforto_pct"], abs=1.0)
    assert r["termico.utci_calor"].item() == pytest.approx(lb["calor_pct"], abs=1.0)
    assert r["termico.utci_frio"].item() == pytest.approx(lb["frio_pct"], abs=0.3)


def test_exposicao_muro_longo(clima):
    """Pessoa a d = 4 m ao sul de um muro de 1 km × 10 m (face ao longo de x): céu e sol
    vistos conferidos pela conta analítica com as mesmas direções."""
    d, H, h = 4.0, 10.0, ce_altura()
    muro = caixa(-500, d, 0, 500, d + 0.3, H)
    raios = Cena([muro])
    ceu = criar_ceu(1)
    sol = posicao_solar_clima(clima)
    dia = sol.altitude > 0
    p = torch.tensor([[0.0, 0.0, h]])
    ceu_exp, vis_sol = ce.exposicao(raios, p, None, ceu.vetores_raio, ceu.angulo_solido,
                                    sol.vetores[dia])

    def bloqueado(vet):
        vy, vz = vet[:, 1], vet[:, 2]
        horiz = vet[:, :2].norm(dim=1)
        t = d / vy.clamp(min=1e-12)                    # distância ao plano do muro
        x = vet[:, 0] * t
        return (vy > 1e-9) & (h + vz * t < H) & (x.abs() < 500) & (horiz > 0)

    esperado = ((~bloqueado(ceu.vetores_raio.double())).double()
                * ceu.angulo_solido / (2 * math.pi)).sum()
    assert ceu_exp.item() == pytest.approx(esperado.item(), abs=1e-9)
    assert 0.3 < ceu_exp.item() < 0.7
    sol_esp = ~bloqueado(sol.vetores[dia].double())
    dif = (vis_sol[0].cpu() != sol_esp).sum().item()
    assert dif <= 2  # só raios rentes ao topo do muro podem divergir (float32)


def ce_altura():
    from otimiza.modulos.termico.modulo import ALTURA_PESSOA
    return ALTURA_PESSOA


@precisa_ref
def test_utci_sombra_melhora_calor(clima):
    """Pessoa sob uma marquise (céu e sol bloqueados em parte) tem menos horas de calor."""
    piso = retangulo(-10, -10, 10, 10)
    marquise = retangulo(-3, -3, 3, 3, z=3.0)
    aberto = variante_de(piso, "entorno")
    v, f = juntar(piso, marquise)
    rot = torch.tensor([CODIGO["entorno"]] * 2 + [CODIGO["contexto"]] * 2)
    sombra = Variante(v, f, rot, torch.full((4,), 0.3), torch.zeros(4))
    r = avaliar(CenaLote([aberto, sombra], clima), ["termico.utci_calor"], "rapido")
    calor = r["termico.utci_calor"].cpu()
    assert calor[1] < calor[0]

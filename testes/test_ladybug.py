"""Comparações contra o Ladybug, usando casos exportados pelo script
rhino/grasshopper/exportar_referencias.py. Pulados se não houver casos."""
import pytest
import torch

from otimiza.nucleo.ceu import criar_ceu
from otimiza.nucleo.clima import ler_epw
from otimiza.nucleo.matriz_ceu import matriz_ceu
from otimiza.nucleo.sol import posicao_solar
from testes.conftest import PASTA_EPW
from testes.referencias import carregar, casos_com

CASOS_SOL = casos_com("sol_ladybug.json")
CASOS_CEU = casos_com("ceu_ladybug.json")
CASOS_MATRIZ = [
    (caso, i) for i in (0, 1) for caso in casos_com("matriz_ceu_ladybug_%d.json" % i)
]


@pytest.mark.skipif(not CASOS_SOL, reason="nenhum sol_ladybug.json em testes/referencias")
@pytest.mark.parametrize("caso", CASOS_SOL, ids=lambda p: p.name)
def test_sol_contra_ladybug(caso):
    ref = carregar(caso, "sol_ladybug.json")
    hoy = torch.tensor(ref["hoy"], dtype=torch.float64)
    dia_do_ano = torch.floor(hoy / 24)
    p = posicao_solar(
        ref["latitude"], ref["longitude"], ref["fuso"],
        mes=1, dia=1 + dia_do_ano, hora=hoy - 24 * dia_do_ano,
    )
    alt_lb = torch.tensor(ref["altitude"], dtype=torch.float64)
    az_lb = torch.tensor(ref["azimute"], dtype=torch.float64)

    dia = alt_lb > 1
    erro_alt = (p.altitude - alt_lb)[dia].abs().max().item()
    # azimute é instável perto do zênite; compara só abaixo de 85°
    ok_az = dia & (alt_lb < 85)
    dif = torch.remainder(p.azimute - az_lb + 180, 360) - 180
    erro_az = dif[ok_az].abs().max().item()
    print(f"\n{caso.name}: erro máx. altitude {erro_alt:.4f}°, azimute {erro_az:.4f}°")
    assert erro_alt < 0.05
    assert erro_az < 0.1


@pytest.mark.skipif(not CASOS_MATRIZ, reason="nenhuma matriz_ceu_ladybug_*.json")
@pytest.mark.parametrize("caso, i", CASOS_MATRIZ, ids=lambda x: getattr(x, "name", str(x)))
def test_matriz_ceu_contra_ladybug(caso, i):
    """LB Cumulative Sky Matrix guarda, por patch, Σ L·Ω no ano (kWh/m²)."""
    ref = carregar(caso, "matriz_ceu_ladybug_%d.json" % i)
    nome_epw = ref["data"][0][5]  # "city : ...", só para mensagem
    epws = sorted(PASTA_EPW.rglob("*.epw"))
    if not epws:
        pytest.skip("EPW do caso não está em data/epw")
    clima = ler_epw(epws[0])
    ceu = criar_ceu(2 if ref["high_density"] else 1)
    m = matriz_ceu(clima, ceu)

    lb_dif = torch.tensor(ref["diffuse_values"], dtype=torch.float64)
    lb_dir = torch.tensor(ref["direct_values"], dtype=torch.float64)
    dif, dir_ = m.acumulado_kwh("difusa"), m.acumulado_kwh("direta")
    assert lb_dif.shape[0] == ceu.n_patches

    r = dif / lb_dif
    print(f"\n{caso.name} MF={ceu.mf} ({nome_epw}): difusa total {dif.sum() / lb_dif.sum():.4f}, "
          f"por patch {r.min():.3f}..{r.max():.3f}, rms {((r - 1) ** 2).mean().sqrt():.3f}; "
          f"direta total {dir_.sum() / lb_dir.sum():.4f}")
    # difusa: medido em Curitiba total +0,7%, patch 0,99..1,06, rms 1,8%
    assert abs(dif.sum() / lb_dif.sum() - 1) < 0.015
    assert ((r - 1).abs() < 0.08).all()
    assert ((r - 1) ** 2).mean().sqrt() < 0.03
    # direta: mesma energia e mesmos patches; repartição entre vizinhos varia
    assert abs(dir_.sum() / lb_dir.sum() - 1) < 0.005
    diferentes = (dir_ > 0) != (lb_dir > 0)  # sol num patch só de um dos dois
    energia_dif = (dir_[diferentes].sum() + lb_dir[diferentes].sum()) / lb_dir.sum()
    assert energia_dif < 0.01


@pytest.mark.skipif(not CASOS_CEU, reason="nenhum ceu_ladybug.json em testes/referencias")
@pytest.mark.parametrize("chave, mf", [("tregenza", 1), ("reinhart", 2)])
def test_ordem_dos_patches_igual_ao_ladybug(chave, mf):
    ref = carregar(CASOS_CEU[0], "ceu_ladybug.json")
    v = torch.tensor(ref[chave], dtype=torch.float64)
    ceu = criar_ceu(mf)
    if v[:, 2].mean() < 0:  # se o Ladybug apontar para baixo, inverte
        v = -v
    assert v.shape[0] == ceu.n_patches, f"Ladybug tem {v.shape[0]} patches"
    idx = ceu.indice(v)
    certos = (idx == torch.arange(ceu.n_patches)).sum().item()
    assert certos == ceu.n_patches, (
        f"{certos}/{ceu.n_patches} patches na mesma posição. "
        f"Primeiros índices do Ladybug no nosso céu: {idx[:12].tolist()}"
    )

"""Comparações contra o Ladybug, usando casos exportados pelo script
rhino/grasshopper/exportar_referencias.py. Pulados se não houver casos."""
import pytest
import torch

from otimiza.nucleo.ceu import criar_ceu
from otimiza.nucleo.sol import posicao_solar
from testes.referencias import carregar, casos_com

CASOS_SOL = casos_com("sol_ladybug.json")
CASOS_CEU = casos_com("ceu_ladybug.json")


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

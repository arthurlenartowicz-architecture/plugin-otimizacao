import pytest
import torch

from otimiza.nucleo.clima import HORAS_ANO, ler_epw
from testes.conftest import EPWS_REAIS, escrever_epw_sintetico


def test_local(epw_sintetico):
    c = ler_epw(epw_sintetico)
    assert c.local.cidade == "Cidade Teste"
    assert c.local.latitude == -23.55
    assert c.local.longitude == -46.63
    assert c.local.fuso == -3.0
    assert c.local.elevacao == 760.0


def test_dados(epw_sintetico):
    c = ler_epw(epw_sintetico)
    assert c["ghi"].shape == (HORAS_ANO,)
    i = torch.arange(HORAS_ANO, dtype=torch.float64)
    assert torch.allclose(c["temp_bulbo_seco"], torch.remainder(i, 400) / 10)
    assert torch.allclose(c["ghi"], torch.remainder(i, 1000))
    # primeira e última linha
    assert (c.mes[0], c.dia[0], c.hora[0]) == (1, 1, 1)
    assert (c.mes[-1], c.dia[-1], c.hora[-1]) == (12, 31, 24)


def test_hora_meio(epw_sintetico):
    c = ler_epw(epw_sintetico)
    assert c.hora_meio[0] == 0.5
    assert c.hora_meio[23] == 23.5


def test_arquivo_incompleto(tmp_path):
    caminho = escrever_epw_sintetico(tmp_path / "curto.epw", n_linhas=100)
    with pytest.raises(ValueError, match="100 linhas"):
        ler_epw(caminho)


def test_nao_e_epw(tmp_path):
    caminho = tmp_path / "x.epw"
    caminho.write_text("isto nao e um epw\n")
    with pytest.raises(ValueError, match="LOCATION"):
        ler_epw(caminho)


@pytest.mark.skipif(not EPWS_REAIS, reason="nenhum EPW real em data/epw")
@pytest.mark.parametrize("caminho", EPWS_REAIS, ids=lambda p: p.name)
def test_epw_real(caminho):
    c = ler_epw(caminho)
    assert -90 <= c.local.latitude <= 90
    assert -180 <= c.local.longitude <= 180
    # radiação fisicamente plausível (9999 = dado faltante no EPW)
    assert c["ghi"].min() >= 0 and c["ghi"].max() < 1400
    assert c["dhi"].max() <= c["ghi"].max() + 1

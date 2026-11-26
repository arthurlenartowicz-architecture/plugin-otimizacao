import pytest
import torch

from otimiza.nucleo.geometria import caixa, normais_e_areas, retangulo, sensores_de_malha


def test_area_e_normal_do_retangulo():
    v, f = retangulo(0, 0, 4, 2)
    n, a = normais_e_areas(v, f)
    assert a.sum().item() == pytest.approx(8.0)
    assert torch.allclose(n, torch.tensor([[0, 0, 1.0]] * 2, dtype=torch.float64))


def test_caixa_normais_para_fora():
    v, f = caixa(0, 0, 0, 1, 1, 1)
    n, a = normais_e_areas(v, f)
    centros = v[f].mean(dim=1)
    assert ((centros - 0.5) * n).sum(dim=1).min() > 0  # normal aponta para fora
    assert a.sum().item() == pytest.approx(6.0)


@pytest.mark.parametrize("esp", [0.25, 0.5, 1.0, 3.0])
def test_sensores_somam_a_area(esp):
    v, f = caixa(0, 0, 0, 2, 3, 1.5)
    s = sensores_de_malha(v, f, esp)
    _, a = normais_e_areas(v, f)
    assert s.areas.sum().item() == pytest.approx(a.sum().item(), rel=1e-12)


def test_sensores_densidade_e_deslocamento():
    v, f = retangulo(0, 0, 10, 10)
    s = sensores_de_malha(v, f, 0.5, deslocamento=0.8)
    assert torch.allclose(s.pontos[:, 2], torch.full((s.n,), 0.8, dtype=torch.float64))
    assert s.pontos[:, :2].min() > 0 and s.pontos[:, :2].max() < 10  # dentro do retângulo
    # ~1 sensor por (0,5 m)² ou mais denso, nunca menos
    assert s.n >= 100 / 0.25
    assert torch.allclose(s.normais.norm(dim=1), torch.ones(s.n, dtype=torch.float64))


def test_sensores_cobrem_uniformemente():
    # média dos pontos = centróide do retângulo (cobertura sem viés)
    v, f = retangulo(0, 0, 6, 2)
    s = sensores_de_malha(v, f, 0.3)
    centro = (s.pontos[:, :2] * s.areas[:, None]).sum(0) / s.areas.sum()
    assert torch.allclose(centro, torch.tensor([3.0, 1.0], dtype=torch.float64), atol=1e-9)

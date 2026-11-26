"""Raycasting no Warp contra resultados analíticos."""
import math
import time

import pytest
import torch

from otimiza.nucleo.ceu import criar_ceu
from otimiza.nucleo.geometria import caixa, retangulo, sensores_de_malha
from otimiza.nucleo.raios import Cena, dispositivo_padrao

DEV = dispositivo_padrao()


def direcoes_aleatorias(n, seed=0, hemisferio=True):
    g = torch.Generator().manual_seed(seed)
    z = torch.rand(n, generator=g) if hemisferio else 2 * torch.rand(n, generator=g) - 1
    phi = 2 * math.pi * torch.rand(n, generator=g)
    r = torch.sqrt(1 - z**2)
    return torch.stack([r * torch.cos(phi), r * torch.sin(phi), z], dim=1)


def test_usa_gpu():
    assert DEV.startswith("cuda"), "Warp não achou a GPU"


def test_distancia_ate_a_caixa():
    cena = Cena([caixa(-1, -1, -1, 1, 1, 1)])
    orig = torch.tensor([[-5.0, 0, 0], [0, 0, 10], [5, 5, 5]])
    dirs = torch.tensor([[1.0, 0, 0], [0, 0, -1], [1, 0, 0]])
    t, face = cena.intersectar(orig, dirs)
    assert t[0].item() == pytest.approx(4.0, abs=1e-4)
    assert t[1].item() == pytest.approx(9.0, abs=1e-4)
    assert math.isinf(t[2].item()) and face[2].item() == -1


def test_sombra_de_cobertura_quadrada():
    # ponto na origem, cobertura [-1,1]² a 1 m de altura:
    # a direção d (dz > 0) é bloqueada se |dx/dz| < 1 e |dy/dz| < 1
    cena = Cena([retangulo(-1, -1, 1, 1, z=1.0)])
    d = direcoes_aleatorias(200_000)
    vis = cena.visivel(torch.zeros_like(d), d).cpu()
    bloqueada = ((d[:, 0] / d[:, 2]).abs() < 1) & ((d[:, 1] / d[:, 2]).abs() < 1)
    # perto da borda o float32 pode decidir para qualquer lado
    longe_da_borda = (((d[:, 0] / d[:, 2]).abs() - 1).abs() > 1e-4) & \
                     (((d[:, 1] / d[:, 2]).abs() - 1).abs() > 1e-4)
    assert torch.equal(vis[longe_da_borda], ~bloqueada[longe_da_borda])


def test_fator_de_ceu_sob_cobertura():
    # fração do hemisfério (por ângulo sólido) vista sob uma cobertura 2x2 m a 1 m.
    # Ângulo sólido de um retângulo 2a x 2b a distância d, visto do centro:
    #   Ω = 4·asin(a·b / sqrt((a² + d²)(b² + d²)))  ->  a = b = d = 1: 4·asin(1/2) = 2π/3
    cena = Cena([retangulo(-1, -1, 1, 1, z=1.0)])
    ceu = criar_ceu(2)
    vis = cena.visibilidade(torch.zeros(1, 3), ceu.vetores).cpu()[0]
    fracao_ceu = (vis.double() * ceu.angulo_solido).sum() / (2 * math.pi)
    esperado = 1 - (4 * math.asin(0.5)) / (2 * math.pi)  # = 2/3
    assert fracao_ceu.item() == pytest.approx(esperado, abs=0.01)  # discretização dos patches


def test_variantes_em_lote():
    # variante 0: cobertura acima; variante 1: nada acima (cobertura deslocada)
    cena = Cena([retangulo(-1, -1, 1, 1, z=1.0), retangulo(10, 10, 12, 12, z=1.0)])
    orig = torch.zeros(4, 3)
    dirs = torch.tensor([[0, 0, 1.0]] * 4)
    var = torch.tensor([0, 1, 0, 1])
    vis = cena.visivel(orig, dirs, variante=var).cpu()
    assert vis.tolist() == [False, True, False, True]


def test_atualizar_vertices_reajusta_bvh():
    v, f = retangulo(-1, -1, 1, 1, z=1.0)
    cena = Cena([(v, f)])
    para_cima = torch.tensor([[0, 0, 1.0]])
    assert not cena.visivel(torch.zeros(1, 3), para_cima).item()
    cena.atualizar(0, v + torch.tensor([5.0, 0, 0]))  # move a cobertura para o lado
    assert cena.visivel(torch.zeros(1, 3), para_cima).item()


def test_direcoes_atras_da_superficie():
    cena = Cena([retangulo(50, 50, 51, 51)])  # nada por perto
    d = torch.tensor([[0, 0, 1.0], [0, 0, -1.0]])
    vis = cena.visibilidade(torch.zeros(1, 3), d, normais=torch.tensor([[0, 0, 1.0]])).cpu()
    assert vis.tolist() == [[True, False]]


def test_benchmark(capsys):
    # 64 variantes de uma quadra com 25 blocos, sensores no chão × 577 patches
    variantes, sensores, var_s = [], [], []
    chao_v, chao_f = retangulo(0, 0, 100, 100)
    for k in range(64):
        g = torch.Generator().manual_seed(k)
        blocos = []
        for i in range(5):
            for j in range(5):
                h = 5 + 40 * torch.rand(1, generator=g).item()
                x, y = 5 + 20 * i, 5 + 20 * j
                blocos.append(caixa(x, y, 0, x + 10, y + 10, h))
        from otimiza.nucleo.geometria import juntar
        variantes.append(juntar((chao_v, chao_f), *blocos))
        s = sensores_de_malha(chao_v, chao_f, 2.0, deslocamento=0.5)
        sensores.append(s.pontos)
        var_s.append(torch.full((s.n,), k))
    cena = Cena(variantes)
    ceu = criar_ceu(2)
    pontos, var = torch.cat(sensores), torch.cat(var_s)
    cena.visibilidade(pontos[:10], ceu.vetores, variante=var[:10])  # aquece/compila
    torch.cuda.synchronize()
    t0 = time.perf_counter()
    vis = cena.visibilidade(pontos, ceu.vetores, variante=var)
    torch.cuda.synchronize()
    dt = time.perf_counter() - t0
    n_raios = pontos.shape[0] * ceu.n_patches
    with capsys.disabled():
        print(f"\nbenchmark: {n_raios / 1e6:.1f} M raios ({pontos.shape[0]} sensores × "
              f"{ceu.n_patches} patches, 64 variantes) em {dt * 1000:.0f} ms "
              f"= {n_raios / dt / 1e6:.0f} M raios/s")
    assert vis.shape == (pontos.shape[0], ceu.n_patches)

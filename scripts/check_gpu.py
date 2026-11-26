"""Etapa 0 — confirma que PyTorch e NVIDIA Warp estão usando a GPU.

Uso:  .venv\\Scripts\\python.exe scripts\\check_gpu.py
"""
import sys
import time

import numpy as np
import torch
import warp as wp


def check_torch() -> bool:
    print(f"PyTorch {torch.__version__} | CUDA build {torch.version.cuda}")
    if not torch.cuda.is_available():
        print("  ERRO: torch.cuda.is_available() == False")
        return False
    dev = torch.device("cuda")
    props = torch.cuda.get_device_properties(dev)
    print(f"  GPU: {props.name} | sm_{props.major}{props.minor} | {props.total_memory / 2**30:.1f} GB")

    n = 8192
    a = torch.randn(n, n, device=dev)
    b = torch.randn(n, n, device=dev)
    _ = a @ b  # aquecimento (inicializa cuBLAS)
    torch.cuda.synchronize()
    t = time.perf_counter()
    for _ in range(10):
        c = a @ b
    torch.cuda.synchronize()
    dt = time.perf_counter() - t
    tflops = 10 * 2 * n**3 / dt / 1e12
    print(f"  matmul {n}x{n} x10: {dt:.3f}s  (~{tflops:.1f} TFLOPS FP32) em {c.device}")
    return True


@wp.kernel
def saxpy(x: wp.array(dtype=float), y: wp.array(dtype=float), a: float):
    i = wp.tid()
    y[i] = a * x[i] + y[i]


def check_warp() -> bool:
    wp.init()
    if not wp.is_cuda_available():
        print("  ERRO: Warp não encontrou CUDA")
        return False
    dev = wp.get_preferred_device()
    print(f"Warp {wp.config.version} | dispositivo {dev} ({dev.name})")
    n = 1_000_000
    x = wp.array(np.ones(n, dtype=np.float32), device=dev)
    y = wp.array(np.full(n, 2.0, dtype=np.float32), device=dev)
    wp.launch(saxpy, dim=n, inputs=[x, y, 3.0], device=dev)
    ok = np.allclose(y.numpy(), 5.0)
    print(f"  kernel saxpy em {n} elementos: {'OK' if ok else 'FALHOU'}")
    return ok


if __name__ == "__main__":
    ok = check_torch() and check_warp()
    print("\nRESULTADO:", "GPU OK" if ok else "FALHA")
    sys.exit(0 if ok else 1)

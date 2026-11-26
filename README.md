# Plugin de otimização na GPU para Rhino 8 (TCC)

Calcula objetivos de desempenho na GPU (sol/luz, térmico, vento via XLB/LBM,
estrutura FEM, carbono/custo, programa), otimiza com NSGA-II/III e usa IA
substituta (PhysicsNeMo) onde a física é cara.

## Estrutura

```
src/otimiza/
  nucleo/        cena em lote, clima EPW, cache, dependências
  modulos/       um pacote por tema, todos com o mesmo contrato
    sol_luz/ termico/ vento/ estrutura/ acustica/ carbono_custo/ programa/
  otimizacao/    NSGA-II/III, RVEA, análise do Pareto
  ia/            modelos substitutos (PhysicsNeMo)
rhino/           plugin C# para Rhino 8
scripts/         utilitários (check_gpu.py)
testes/          validação de cada módulo
data/epw/        arquivos climáticos (não versionados)
docs/ notebooks/
```

## Ambiente

- Windows, RTX 5070 (Blackwell, sm_120), driver 621.24
- Python 3.12, PyTorch 2.11.0+cu128, NVIDIA Warp 1.19.0

```powershell
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install torch --index-url https://download.pytorch.org/whl/cu128
.venv\Scripts\python.exe -m pip install warp-lang numpy
.venv\Scripts\python.exe scripts\check_gpu.py
```

Versões exatas em `requirements-lock.txt`.

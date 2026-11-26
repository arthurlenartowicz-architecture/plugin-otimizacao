# Plugin de otimização na GPU para Rhino (TCC)

## Objetivo
Plugin próprio para Rhino 8 que calcula objetivos de desempenho arquitetônico
na GPU, otimiza com algoritmos multiobjetivo e usa IA substituta onde a física
é cara. Parte do meu TCC de arquitetura. Sou estudante, não programador
experiente: explique o essencial de cada passo, sem exagero.

## Máquina
- Windows, GPU NVIDIA RTX 5070 (Blackwell, 12 GB VRAM)
- Exige CUDA 12.8+ e builds recentes de PyTorch e Warp (sm_120)
- O motor de cálculo roda local, na GPU

## Arquitetura
Ciclo: Rhino (parâmetros) → núcleo → módulos → otimizador → aplica solução no Rhino

- `nucleo/`: cena em lote (N variantes ao mesmo tempo na GPU), clima (EPW,
  posição solar, matriz de céu), parâmetros (contínuos, inteiros,
  categóricos), cache e resolução de dependências entre módulos
- `modulos/`: um por tema, todos com o mesmo contrato
  (sol_luz, termico, vento, estrutura, acustica, carbono_custo, programa)
- `otimizacao/`: NSGA-II (até 3 objetivos), NSGA-III/RVEA (4 ou mais),
  coordenadas paralelas, clusters do Pareto
- `ia/`: modelos substitutos (PhysicsNeMo), usados só onde a física é cara
- `rhino/`: plugin C# (interface, leitura da geometria, visualização)
- `testes/`: validação de cada módulo

## Contrato de módulo
Cada módulo declara `nome`, `depende_de`, `metricas` (unidade e sentido
min/max) e implementa `avaliar(cena: CenaLote, cache) -> dict[str, Tensor]`,
com um valor por variante. Tudo roda em lote. Níveis de precisão:
`rapido` | `padrao` | `validacao`.
Um problema de otimização é uma lista de objetivos e restrições no formato
`"modulo.metrica"`, e o motor roda só os módulos necessários.

## Stack
Python 3 · PyTorch (CUDA 12.8+) · NVIDIA Warp · XLB (vento/LBM) ·
PhysicsNeMo (IA) · pymoo (otimização) · C# / RhinoCommon (plugin)

## Etapas
- [x] 0. Ambiente: CUDA, PyTorch, Warp, teste de GPU, estrutura, git
- [ ] 1. Sol e luz + conforto externo (validar contra o Ladybug)
- [ ] 2. Estrutura: FEM de barras em lote (validar contra o Karamba)
- [ ] 3. Otimizador + Pareto no Rhino → MVP do TCC
- [ ] 4. Plugin C# completo + térmico, carbono/custo, programa
- [ ] 5. Vento (XLB) + IA substituta

## Regras de trabalho
- Sempre em português (código e nomes de variáveis podem ser em português)
- Economize uso: edite trechos em vez de reescrever arquivos inteiros, e leia
  só os arquivos necessários
- Todo módulo novo precisa de teste de validação contra uma referência
  (Ladybug, Karamba ou resultado analítico) antes de ser dado como pronto
- Peça confirmação antes de instalar pacotes grandes ou mudar a arquitetura
- Nunca versionar no git: resultados de simulação, dados de treino, modelos
  treinados, arquivos .3dm grandes (manter no .gitignore)
- Ao terminar uma etapa: marcar o checkbox acima e registrar as decisões abaixo

## Decisões registradas
- Etapa 0 (2026-11-26): Python 3.12.10 em `.venv`, PyTorch 2.11.0+cu128,
  Warp 1.19.0, driver 621.24. Versões exatas em `requirements-lock.txt`.
  Teste: `.venv\Scripts\python.exe scripts\check_gpu.py` (~24 TFLOPS FP32).
- Código Python fica no pacote `src/otimiza/` (`nucleo/`, `modulos/`,
  `otimizacao/`, `ia/`); `rhino/` e `testes/` ficam na raiz.
- Repositório privado: github.com/arthurlenartowicz-architecture/plugin-otimizacao
  (branch `main`).

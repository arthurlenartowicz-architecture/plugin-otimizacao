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
- [ ] 1. Sol e luz
  - [x] 1a · Fundação: leitor de EPW, posição solar, subdivisão do céu
    (Tregenza/Reinhart), matriz de céu anual (modelo de Perez, equivalente ao
    `gendaymtx` do Radiance), malhas de sensores, estrutura de aceleração (BVH)
    e raycasting no Warp, importação da geometria do Rhino.
  - [x] 1b · Motor A: radiação incidente, horas de sol, sombra no entorno e
    fotovoltaico. Validação contra o Ladybug (Incident Radiation, Sunlight Hours).
  - [x] 1c · Motor B: matriz de coeficientes com reflexões, depois fator de luz
    diurna, DA, sDA, UDI e ASE. Validação contra o Honeybee-Radiance.
  - [x] 1d · EML: fatores melanópicos sobre o motor B.
  - [ ] 1e · Motor C: render olho-de-peixe e fórmula do DGP (Wienold).
    Validação contra o evalglare.
  - [ ] 1f · Integração: tudo no contrato de módulo (`sol_luz`), com os níveis
    rápido, padrão e validação, e um benchmark de quantas variantes por segundo
    a 5070 aguenta.
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
- Testes: `.venv\Scripts\python.exe -m pytest` (config no `pyproject.toml`).
- Posição solar (`nucleo/sol.py`): algoritmo da NOAA/Meeus em float64, com
  refração. Erro contra o caso de exemplo do SPA/NREL: 0,0007° zênite,
  0,0023° azimute. Ano fixo 2017 (não bissexto), como o Ladybug. Para dados
  horários do EPW, o sol é calculado no meio da hora (h − 0,5).
- Convenções: azimute a partir do norte, horário; vetor solar aponta do ponto
  para o sol, eixos do Rhino (x leste, y norte, z cima) — oposto ao Ladybug.
- EPW (`nucleo/clima.py`): exige 8760 linhas; lê em UTF-8 ou latin-1.
- Céu (`nucleo/ceu.py`): lógica do `reinhartb.cal` (MF=1 Tregenza 145,
  MF=2 Reinhart 577), sem patch de chão. Ordem assumida: faixas do horizonte
  ao zênite, 1º patch centrado no norte, seguindo para o leste.
  Confirmado contra o Ladybug (`view_sphere`, caso curitiba): os 145 e os 577
  patches na mesma ordem; os vetores do Ladybug também apontam para cima.
- Sol validado contra o Ladybug (Curitiba, 8760 h): erro máx. 0,0014° na
  altitude e 0,0024° no azimute.
- Matriz de céu (`nucleo/matriz_ceu.py`): Perez 1993, equivalente ao
  `gendaymtx -O1` (só radiação; visível fica para a 1c). Normaliza para que a
  difusa horizontal = DHI somando os patches (não por integral contínua) e
  reparte a DNI nos 4 patches mais próximos do sol (peso 1/(1,002 − cos γ)).
  Horas com sol abaixo do horizonte = zero. Sem patch de chão.
  Comparado com o LB Cumulative Sky Matrix (Curitiba, Tregenza e Reinhart):
  direta total ±0,02%; difusa total +0,7%, por patch 0,99–1,06 (rms 1,8%),
  um pouco mais clara perto do horizonte. O Ladybug também normaliza pela soma
  dos patches (integral fina piorou) e usa o sol no meio da hora (na hora
  cheia o rms sobe para 6,5%). Diferença residual aceita; rever se a 1b
  (radiação em fachadas) passar de ±5% contra o Ladybug.
- Geometria (`nucleo/geometria.py`): malhas trianguladas (vertices, faces) em
  metros. Sensores: cada triângulo dividido em n² sub-triângulos iguais
  (n = maior aresta / espaçamento), sensor no centro de cada um, com área
  igual → somar valor × área dá o total. Para comparar com o Ladybug, usar os
  pontos exportados do LB Generate Point Grid, não os nossos.
- Raios (`nucleo/raios.py`): `Cena` = um `wp.Mesh` (BVH do Warp) por
  variante; cada raio leva o índice da variante, tudo num só kernel.
  Geometria em float32 na GPU, origem afastada 1e-4 m. `atualizar()` move
  vértices e faz refit da BVH (mesma topologia, mais rápido que reconstruir).
  Benchmark: ~1,6 bilhão de raios/s (64 variantes, 645 mil sensores × 577
  patches em 0,23 s).
- Motor A (`modulos/sol_luz/motor_a.py`): mesmo método do Ladybug — céu como
  direções dos centros dos patches com energia acumulada (sol direto já nos
  patches); radiação = Σ visível · cos⁺ · E_p. Solo = patches espelhados com
  L = ρ·G_horiz/π (sem reflexões entre edifícios; isso é o motor B).
  FV simples: radiação × área × eficiência × (1 − perdas).
- Validação 1b (Curitiba, prédio + piso + torre, 1150 sensores do LB):
  horas de sol 100% iguais; radiação erro médio 0,5%, máx 5%. Sensores
  "encostados" (centro tocando outra superfície, ex.: piso na base de parede)
  são ambíguos para qualquer motor e ficam fora (`sensores_encostados`).
- Direções dos raios ≠ centros do céu: o Ladybug avalia o céu no meio da faixa
  mas lança raios mais baixos (até 1,8° perto do zênite). Usamos
  `Ceu.vetores_raio` = altitude do centro de área da faixa (sen = média dos
  senos), a <0,5° dos vetores do LB. Matriz de céu continua no meio da faixa.
- Solo: o LB dá a cada patch de solo ρ·(Σ energia do céu)/n_patches (não é
  lambertiano). Padrão nosso = lambertiano (ρ·G_horiz·Ω/π, físico), ~15% a
  mais de solo nas fachadas; `modelo_solo="ladybug"` para comparar.
- Céu visível (`matriz_ceu(..., luz=True)`): eficácia luminosa de Perez 1990
  (ε e Δ sempre radiométricos; água precipitável pelo ponto de orvalho). Em
  Curitiba bate com os campos do EPW: difusa 1,04, direta normal 0,99.
  O EPW do INMET é incoerente: GHI medida 26% acima de DHI + DNI·sen(alt);
  nós (como Radiance/Honeybee) usamos DNI + DHI.
- Motor B (`modulos/sol_luz/motor_b.py`): esquema do annual daylight do
  Honeybee = C_dir·céu difuso + C_ref·(céu + sol nos patches) + sol direto
  exato. Coeficientes por path tracing no Warp (cosseno, reflexão difusa com ρ
  por face, vidro reto com τ(θ) de Fresnel n=1,52 calibrado para τ normal).
  Última coluna = solo, L = ρ·E_horiz/π. Horas ocupadas 8–18 h (3650 h).
  Métricas: DA300, UDI 100–3000, sDA (DA ≥ 50%), ASE (sol direto > 1000 lux
  por > 250 h), DF com céu CIE encoberto. Sala 722 sensores × 8192 raios ×
  5 reflexões: 8 ms.
- Validação 1c contra Honeybee-Radiance (sala 6x8x3 m, janelas 40%, materiais
  padrão do HB: parede 0,5, piso 0,2, teto 0,8, vidro τ 0,64; -ab 5; Curitiba):
  DF médio −2,7% (por sensor 2,7% médio, 9% máx); DA300 0,08 ponto; UDI
  0,9 ponto (máx ~9 perto do limite de 3000 lux). Geometria, materiais e
  sensores vêm do próprio HB Model exportado (`sala_hb.json`). No GH a grade
  entra pelo HB Assign Grids and Views. ASE não comparado direto com o HB
  (usa o mesmo sol direto já validado nas horas de sol).
- Melanópico (1d, `motor_b.py`): canal extra do motor B. Materiais com
  ρ_mel/τ_mel opcionais (None = neutro); céu difuso e sol com DER (razão de
  eficácia melanópica relativa ao D65) separados, padrão 1,0 = supõe D65
  (não achei tabela confiável de DER por espectro; passar valores da
  literatura quando houver). Grandeza m-EDI (CIE S 026); EML = 1,104·m-EDI.
  Sensores verticais a 1,2 m (`sensores_olho`, 4 direções). Métrica padrão:
  m-EDI ≥ 250 lux (Brown et al. 2022) em % das horas e % dos dias com ≥ 4 h.
  Validação analítica (piso colorido, DER separando céu e sol).
- Motor C (1e, `motor_c.py`): imagem olho-de-peixe angular 180° (n × n),
  coeficientes por pixel (mesmo path tracer, peso 1/M = luminância), sol como
  fonte à parte do tamanho real (ω = 6,8e-5 sr). DGP de Wienold 2006; fontes
  = pixels > 2000 cd/m² (como dcglare/HB imageless; < 100 = fator × média);
  P de Guth acima e Iwata abaixo da linha de visão (máx 16); correção de baixa
  luz (Ev < 1000) opcional. GA = % horas ocupadas com DGP < 0,4. 8 vistas ×
  12,9 mil pixels × 64 amostras: 12 ms. PENDENTE: comparar com o HB Imageless
  Annual Glare (2º modelo HB no .gh, grade de 8 vistas).
- Referências do Ladybug: `rhino/grasshopper/montar_definicao.py` monta a
  definição no GH; os JSON ficam em `testes/referencias/<caso>/` (versionados,
  são o gabarito dos testes).

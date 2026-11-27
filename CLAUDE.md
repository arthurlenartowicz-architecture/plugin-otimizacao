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
- [x] 1. Sol e luz
  - [x] 1a · Fundação: leitor de EPW, posição solar, subdivisão do céu
    (Tregenza/Reinhart), matriz de céu anual (modelo de Perez, equivalente ao
    `gendaymtx` do Radiance), malhas de sensores, estrutura de aceleração (BVH)
    e raycasting no Warp, importação da geometria do Rhino.
  - [x] 1b · Motor A: radiação incidente, horas de sol, sombra no entorno e
    fotovoltaico. Validação contra o Ladybug (Incident Radiation, Sunlight Hours).
  - [x] 1c · Motor B: matriz de coeficientes com reflexões, depois fator de luz
    diurna, DA, sDA, UDI e ASE. Validação contra o Honeybee-Radiance.
  - [x] 1d · EML: fatores melanópicos sobre o motor B.
  - [x] 1e · Motor C: render olho-de-peixe e fórmula do DGP (Wienold).
    Validação contra o evalglare.
  - [x] 1f · Integração: tudo no contrato de módulo (`sol_luz`), com os níveis
    rápido, padrão e validação, e um benchmark de quantas variantes por segundo
    a 5070 aguenta.
- [ ] 2. Estrutura: FEM de barras em lote (validar contra o Karamba)
  - [x] 2a · FEM de barras (treliça e pórtico 3D) em lote na GPU, módulo
    `estrutura` no contrato, testes analíticos.
  - [ ] 2b · Validação contra o Karamba (script pela API pronto; falta rodar
    `montar_karamba.py` no Rhino, apertar `rodar` e o `testes/test_karamba.py`).
- [ ] 3. Otimizador + Pareto no Rhino → MVP do TCC
- [ ] 4. Plugin C# completo + térmico, carbono/custo, programa
  - Objetivos do módulo `termico` (🟢 = direto na GPU; 🟡 = modelo simplificado):
    - [ ] carga de resfriamento e aquecimento, kWh/ano 🟡 (modelo RC em lote
      por zona; ganhos solares vêm do `sol_luz`)
    - [ ] consumo energético total, EUI kWh/m²·ano 🟡 (cargas ÷ COP/eficiência
      + iluminação usando a autonomia de luz natural do `sol_luz` + equipamentos)
    - [ ] conforto interno 🟡: graus-hora / horas de desconforto, conforto
      adaptativo (ASHRAE 55 / EN 16798), PMV/PPD
    - [ ] conforto externo, UTCI 🟢 sem o vento: temperatura radiante média a
      partir da radiação do `sol_luz` (motor A) + ar, umidade e vento do EPW.
      Era parte da Etapa 1 ("conforto externo") e não foi feito lá
    - [ ] ganho solar pelas aberturas, W/m² e WWR por orientação 🟢 (radiação
      nos vidros × τ/FS; WWR direto da geometria)
    - [ ] compacidade, superfície ÷ volume 🟢 (só geometria)
    - [ ] balanço energético zero, geração − consumo kWh/ano 🟡 (depende de
      `sol_luz.fv` e de `termico.eui`: exemplo de `depende_de`)
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
  12,9 mil pixels × 64 amostras: 12 ms.
- Validação 1e contra HB Imageless Annual Glare (dcglare; mesma sala da 1c,
  8 vistas a 1,2 m, -ab 5): GA dif. média 1,5 ponto (máx 3,6); DGP hora a
  hora |dif| média 0,011, p95 0,05, correlação 0,979, 91% na mesma classe.
  O dcglare aplica a correção de baixa luz (padrão ligado). Erros grandes
  isolados = sol rente à borda da janela (vê/não vê o disco). O HB grava
  `<grade>.dgp` (vistas × horas com sol) e `sun-up-hours.txt` (meio da hora).
- Integração (1f): `nucleo/contrato.py` (Metrica, Modulo, registro),
  `nucleo/cena.py` (CenaLote: variantes com rótulo e material por face —
  fachada, vidro, cobertura, piso, interno, contexto, entorno),
  `nucleo/cache.py` (sol e matrizes de céu por clima, compartilhados),
  `nucleo/motor.py` (avaliar "modulo.metrica", dependências em ordem;
  `avaliar_problema` devolve objetivos para minimizar + violação),
  `nucleo/prototipos.py` (prédio paramétrico com janelas por fachada).
  `modulos/sol_luz/modulo.py`: radiacao_fachada, radiacao_cobertura, fv,
  perda_sol_entorno, da, sda, udi, ase, df, medi_horas, ga; só roda o grupo
  das métricas pedidas. Sensores internos a 0,8 m (normal para cima), olhos a
  1,2 m. Níveis rápido/padrão/validação mudam grade, raios, reflexões, MF e
  resolução do olho-de-peixe (tabela NIVEIS no módulo).
- `sensores_de_malha` corrigido: n = √área/espaçamento (antes usava a maior
  aresta, que num retângulo é a diagonal e dava ~4× mais sensores).
- Benchmark 1f (64 variantes de sala com torre, RTX 5070): rápido 27
  variantes/s com tudo (sem GA > 900/s); padrão 7,7/s com tudo (sem GA >
  200/s). O GA domina; se pesar no otimizador, reduzir horas (amostras
  representativas) ou vistas.
- Referências do Ladybug: `rhino/grasshopper/montar_definicao.py` monta a
  definição no GH; os JSON ficam em `testes/referencias/<caso>/` (versionados,
  são o gabarito dos testes).
- Estrutura (2a, `modulos/estrutura/fem.py`): unidades kN, m (massa kg,
  tensões kN/m²). Pórtico de Euler-Bernoulli 12 GL e treliça (só axial; não
  subdividida; rotações de nós só de treliça travadas). Eixos locais como o
  Karamba (a conferir): x = i→j, y = Z × x, z = x × y; barra vertical: z = X.
  Cargas: nodais, distribuída uniforme global por barra (cargas equivalentes
  consistentes) e peso próprio ρ·g·A (`gravidade` em m/s², 0 = sem). Um caso
  de carga. Lote: variantes completadas até o mesmo nº de GL (GL extras e
  apoios por eliminação), K denso float64 por scatter, Cholesky em lote
  (`torch.linalg`), divide em lotes para caber na memória (1,5 GB). Mecanismo
  (pivô < 1e-11 · máx. diagonal) → NaN. Flambagem linear: K φ = λ(−Kg) φ com
  Kg cúbica (N médio do elemento), maior autovalor de L⁻¹(−Kg)L⁻ᵀ via `eigvalsh`.
  Utilização = máx. (|N|/A + flexão elástica)/fy em 5 pontos por elemento
  (tubo: √(My²+Mz²)/W; retângulo: soma) — sem flambagem local/EC3.
  Níveis: barras de pórtico em 1 / 2 / 4 elementos (rápido/padrão/validação);
  nos nós o resultado é exato com qualquer subdivisão.
- `CenaLote.estruturas`: uma `Estrutura` por variante, à parte da malha (cena só
  de estrutura: `variantes=[]`, `clima=None`). Import de `Materiais` em
  `cena.py` passou a ser local (evita import circular).
- Métricas do `estrutura`: deslocamento_max (cm), energia (kN·m), massa (kg),
  utilizacao (%), flambagem (fator, max). Testes analíticos (`test_fem.py`):
  console (ponta, axial, torção, peso próprio), biapoiada com distribuída,
  treliças (tripé, triângulo), Euler (console e biarticulado, n_sub 8: < 1e-4),
  equilíbrio, mecanismo, lote = individual.
- Benchmark 2a (256 variantes, RTX 5070): pórtico 16 nós ~7800 variantes/s
  (rápido), ~4200/s (padrão); treliça 42 nós ~5200/s; com flambagem ~200/s
  (o `eigvalsh` em lote domina; se pesar, trocar por iteração de subespaço).
- Validação Karamba (2b): casos em `testes/referencias/karamba/entrada_<caso>.json`
  (`scripts/gerar_casos_karamba.py`; pórtico espacial 2 pav. engastado e malha
  espacial 4×3 articulada nos cantos; tubo RO114.3/4 e aço S235, os padrões do
  Karamba; só cargas nodais). O 1º montador (componentes do Karamba no canvas,
  dentro da definição do LB/HB) travava o Rhino: refeito como definição à parte
  (`montar_karamba.py` → `validar_karamba.gh`) com UM componente GhPython
  (`karamba_validar.py`) que usa a API do Karamba 3.1.60921, conferida por
  reflexão do `KarambaCommon.dll` instalado e pelos exemplos em
  `Examples/Scripts` (o guia online estava fora do ar): `Toolkit().Part.LineToBeam`
  (bending=False = treliça), `CroSec.CircularHollow` (cm), `FemMaterial.defaultSteel()`,
  `Support.Support(Point3, conds)`, `Load.PointLoad(Point3, F, M, "LC0", False)`,
  `Model.AssembleModel(..., points=nós)`, `Algorithms.Analyze.solve(m, ["LC0"])`,
  `Results.NodeDisplacements.solve`, `Results.BeamForces.solve(..., ts)`,
  `Algorithms.Buckling.solve(m, 1, 3, 300, 1e-7, 1)`. Saídas com out em
  IronPython: tupla (finais) ou `clr.Reference` (no meio). Seção sempre em cm
  (a troca automática para m pela massa errou na 1ª rodada: seção 10⁴ menor).
  O json do IronPython quebra com acentos (arquivo saía cortado): textos são
  passados para ASCII antes de gravar. O componente do `.gh` só carrega o
  `karamba_validar.py` do disco (correções valem sem remontar). Licença do
  usuário: lic_student. O teste casa nós por posição, deduz unidades e usa as
  áreas de cisalhamento (Ay, Az) informadas pelo Karamba.
- Deformação por cortante (Timoshenko) opcional no nosso elemento: `Secao.Ay/Az`
  (0 = Euler-Bernoulli, o padrão), φ = 12EI/(G·As·L²). O Karamba usa Timoshenko;
  no pórtico de teste isso muda o deslocamento em ~0,7%. Kg (flambagem) continua
  a de Euler-Bernoulli. `props` passou a ter 12 colunas (… rho, Ay, Az).

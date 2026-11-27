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
- `otimizacao/`: NSGA-II (2 objetivos), NSGA-III/RVEA (3 ou mais),
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
- [x] 2. Estrutura: FEM de barras em lote (validar contra o Karamba)
  - [x] 2a · FEM de barras (treliça e pórtico 3D) em lote na GPU, módulo
    `estrutura` no contrato, testes analíticos.
  - [x] 2b · Validação contra o Karamba3D 3.1 (pórtico espacial e treliça
    espacial): deslocamentos, rotações, energia e esforços com erro ~1e-13.
- [x] 3. Otimizador + Pareto no Rhino → MVP do TCC
  - [x] 3b · Parâmetros e geometria do Grasshopper (servidor + componente Otimizar);
    conferido no Rhino 8 (validação, otimização e `aplicar` do Pareto)
- [ ] 4. Plugin C# completo + térmico, carbono/custo, programa
  - Plano aprovado (2026-11-27): 4a térmico 🟢 → 4b modelo RC (cargas, EUI, conforto,
    balanço zero; validar contra EnergyPlus via honeybee_energy) → 4c plugin C# (troca só o
    `otimizar_gh.py`, mesmo protocolo v1; precisa do .NET 8 SDK) → 4d carbono/custo → 4e programa.
  - [x] 4a · térmico 🟢: compacidade, WWR, ganho solar, UTCI (módulo `termico`)
  - Objetivos do módulo `termico` (🟢 = direto na GPU; 🟡 = modelo simplificado):
    - [ ] carga de resfriamento e aquecimento, kWh/ano 🟡 (modelo RC em lote
      por zona; ganhos solares vêm do `sol_luz`)
    - [ ] consumo energético total, EUI kWh/m²·ano 🟡 (cargas ÷ COP/eficiência
      + iluminação usando a autonomia de luz natural do `sol_luz` + equipamentos)
    - [ ] conforto interno 🟡: graus-hora / horas de desconforto, conforto
      adaptativo (ASHRAE 55 / EN 16798), PMV/PPD
    - [x] conforto externo, UTCI 🟢 sem o vento: temperatura radiante média a
      partir da radiação do `sol_luz` (motor A) + ar, umidade e vento do EPW.
      Era parte da Etapa 1 ("conforto externo") e não foi feito lá
    - [x] ganho solar pelas aberturas, W/m² e WWR por orientação 🟢 (radiação
      nos vidros × τ/FS; WWR direto da geometria)
    - [x] compacidade, superfície ÷ volume 🟢 (só geometria)
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
- Resultado 2b (2026-11-27, Karamba3D 3.1.60921, licença student): com as áreas
  de cisalhamento do Karamba (Ay = Az = 0,637·A no tubo RO114.3/4) o nosso FEM
  bate com erro ~1e-13 em deslocamento máximo, translações e rotações nodais,
  energia e esforços máximos por barra (N, Mt, M) nos dois casos; flambagem:
  treliça igual, pórtico 12,974 (Karamba) × 12,972 (nós), 0,015% (a Kg nossa é
  de Euler-Bernoulli). Sem cisalhamento (Euler-Bernoulli) o pórtico desloca
  0,5% menos. Massa do Karamba sai em t (kN/(m/s²)); seção A e Iyy em m², m⁴;
  E em kN/m²; resultados em m, kN, kN·m, rad. Nós do Karamba na mesma ordem
  dos nossos (1ª aparição nas barras). Gabarito versionado em
  `testes/referencias/karamba/karamba_<caso>.json`.
- Etapa 3 (2026-11-27): pymoo 0.6.2 instalado (traz scipy, matplotlib; lock
  atualizado). `nucleo/parametros.py`: `Continuo`, `Inteiro`, `Categorico`,
  `Espaco`. O otimizador vê só u ∈ [0, 1] por parâmetro; inteiro/categórico =
  faixas iguais de u (sem viés), e um Repair do pymoo leva os discretos ao centro
  da faixa (mesmo projeto = mesmo vetor → duplicatas eliminadas). Operadores
  padrão (SBX + mutação polinomial) para todos os tipos. Gerador = função
  dict → Variante ou Estrutura; `cena_de` monta a CenaLote da população
  (`prototipos.predio_de`, `estrutura.prototipos.trelica_pratt_de` + catálogo `TUBOS`).
- Otimizador (`otimizacao/otimizador.py`): `otimizar(espaco, avaliador, nomes, ...)`,
  avaliador = lista de dicts → (F para minimizar, violação), uma chamada por
  geração; `avaliador_motor`/`otimizar_problema` ligam ao `motor.avaliar_problema`
  (cache do clima mantido entre gerações). `auto` = NSGA-II com 2 objetivos,
  NSGA-III com 3+ (direções de referência "energy", nº = população); RVEA opcional.
  Restrição única G = violação. NaN/inf → F = 1e12 e violação 1e6 (inviável).
  Todas as avaliações ficam no `Resultado`; a frente sai desse arquivo (viáveis,
  sem repetidas, não dominadas), `F_final` = população final. `objetivos` volta
  ao sentido original (max positivo).
- Pareto (`otimizacao/pareto.py`): não dominadas (pymoo), IGD, hipervolume,
  clusters k-means nos objetivos normalizados (representante = mais perto do
  centro; numerados pelo 1º objetivo), coordenadas paralelas (PNG, objetivos +
  parâmetros), `exportar` → `resultado.json` (soluções em ordem do 1º objetivo,
  no máx. 200, com geometria) + `paralelas.png`.
- Validação 3 (`testes/test_otimizacao.py`): ZDT1/2/3 (NSGA-II, 100 × 200): IGD
  do arquivo ≤ 0,0022, da população ≤ 0,006. DTLZ2 (frente = esfera):
  3 obj NSGA-II dist. à esfera 0,008, IGD 0,077; NSGA-III 0,0008 e IGD 0,0091
  (ideal com 92 pontos 0,0083) → com 3 objetivos o NSGA-III espalha bem melhor;
  por isso o `auto` passou a usar NSGA-III a partir de 3 objetivos (decisão do
  usuário, 2026-11-27; o plano original era NSGA-II até 3);
  5 obj NSGA-III e RVEA no IGD ideal (0,1784 com 126 pontos). Treliça Pratt
  (altura contínua, nº de painéis inteiro, tubo categórico; massa × flecha com
  utilização ≤ 100%): NSGA-II com 400 avaliações chega a 100,05% do hipervolume
  da busca exaustiva (735 projetos). sol_luz rápido (janelas + rotação, sDA × ASE)
  como teste de integração.
- Tempos: treliça 60 × 30 no nível padrão 0,5 s (a 1ª chamada da GPU num processo
  novo leva ~11 s a mais: carga do cuSOLVER); sala com torre, 48 × 20, DA × UDI ×
  ASE no nível rápido: ~10 s.
- Rhino (MVP): `scripts/otimizar_exemplo.py <caso>` grava `resultados/<caso>/`;
  `rhino/grasshopper/montar_pareto.py` monta `pareto.gh` com um GhPython
  (`pareto_rhino.py`, IronPython, lido do disco) que lê o resultado.json e
  mostra a solução escolhida (slider), a frente lado a lado e os parâmetros
  (`valores` para sliders); botão `rodar` chama o Python da `.venv`. Conferido no
  Rhino 8 pelo usuário (2026-11-27): montagem, botão `rodar`, slider, tabela e
  `todas` funcionando; `pareto.gh` versionado (pequeno, como o `validar_karamba.gh`). Os parâmetros
  ainda vêm dos protótipos em Python (geometria do Rhino como parâmetro fica
  para o plugin C#, Etapa 4).
- Etapa 3b (2026-11-27): **GH gera, Python avalia.** `otimizacao.Sessao` = ask/tell do
  pymoo (infill → avaliar → advance, mesma sequência do `minimize`: resultado idêntico
  com a mesma semente); `otimizar()` virou um laço sobre ela. `Continuo.casas` (casas
  do slider): valor decodificado já arredondado e o reparo leva u ao valor arredondado
  (parâmetro gravado = geometria avaliada; repetidos somem).
- Servidor (`src/otimiza/servidor/`): `protocolo.py` (JSON ↔ Espaco, Problema em texto
  "max x.y" / "x.y <= v", Variante por rótulo, Estrutura por linhas + pontos),
  `servico.py` (fila de tarefas + thread única da GPU), `rede.py` (`ThreadingHTTPServer`
  em 127.0.0.1:8765, só stdlib). Escolhas para não haver gargalo: geometria em base64
  float32/int32 (sai direto do `ToFloatArray`/`ToIntArray` da RhinoCommon, sem laço em
  IronPython); contexto/entorno mandados uma vez (`fixas`) e fora do resultado.json;
  projetos repetidos saem do cache (não voltam ao GH); fila com reatribuição (120 s)
  para vários Rhinos/Compute no futuro sem mudar o protocolo; avaliação na thread da
  GPU enquanto o HTTP responde "aguardar". Medido (sala, rápido, DA+UDI+ASE): HTTP +
  decodificação 0,18 ms/variante, GPU 6,6 ms/variante → o custo fica no recálculo do GH.
- Componente Otimizar (`otimizar_gh.py`, IronPython lido do disco; `montar_otimizar.py`
  → `otimizar.gh` com a `sala_exemplo.py`): genoma = sliders (Integer → inteiro,
  Even/Odd → categórico, demais → contínuo com casas) e Value Lists (nomes). Laço num
  `Eto.Forms.UITimer` (fora do SolveInstance, blocos de 0,25 s): seta os sliders com
  `RaiseEvents = False` + `ExpireSolution(False)`, `NewSolution(False)`, e o próprio
  componente captura a geometria no seu SolveInstance (modo captura). Vista sem redraw
  durante o laço; Esc/parar gravam o parcial; no fim restaura os sliders. Breps malhados
  com `MeshingParameters.Minimal` (planos: poucos triângulos; os sensores vêm da nossa
  subdivisão). Pareto ganhou `aplicar` (acha os sliders pelo GUID em `info.cliente`).
- Grade de sensores: `ceil(√área/espaçamento)` com folga relativa de 1e-6 (6,0000001 de
  ruído de float, ex.: geometria em mm, dava 7 divisões e mudava a ASE). Todos os
  testes de validação seguem iguais.
- Testes 3b: `test_servidor.py` (protocolo; dois clientes falsos por HTTP na mesma fila;
  servidor = `otimizar_problema` direto, mesma semente; erro de geometria → inviável;
  sala pelo protocolo em mm com contexto em `fixas` = protótipo nas 6 métricas do
  sol_luz) e `test_gh.py` (sala exportada pelo `avaliar` do GH × `predio_de`).
- Validação GH (2026-11-27, Rhino 8, sala com janela N 40%, rotação 0): áreas e normais
  por rótulo iguais ao protótipo; no nível validação radiação na cobertura igual
  (731,98), fachada +0,15%, DA −0,04, UDI +0,01, sDA −0,35 ponto. No rápido a cobertura
  deu −2,3%: o `MeshingParameters.Minimal` sem SimplePlanes fez uma grade (32
  triângulos na cobertura, 182 na fachada × 2 e 14 no protótipo) e com a grade de 2 m
  os sensores caem em outros lugares. Por isso o teste compara no validação e o
  `minima` do componente liga `SimplePlanes`. Malhar + converter a sala no GH: ~1 ms.
  Gabarito em `testes/referencias/gh/variante_sala_gh.json` (versionado).
- Otimização pelo GH (2026-11-27, Rhino 8, sala de exemplo, 5 sliders, DA × UDI × ASE,
  rápido, NSGA-III 48 × 20): 960 avaliações em 19,2 s, 959 geradas no GH (1 do cache).
  GH 15,8 ms/variante (sliders → NewSolution → malha → base64) = 79% do tempo; GPU
  1,3 ms/variante = 6%; o resto é HTTP e espera do timer. O gargalo é o recálculo do GH,
  como previsto: para acelerar, vários Rhinos/Compute na mesma fila (protocolo pronto).
- `otimizar.gh` traz Otimizar e Pareto na mesma definição (`montar_otimizar.py` chama
  `adicionar_pareto` do `montar_pareto.py`, carregado com `_SO_FUNCOES`): cada montador
  criava um documento novo e um "fechava" o outro. Conferido no Rhino 8 (2026-11-27):
  Pareto recarrega no fim da otimização e o `aplicar` leva a solução aos sliders.
- Modo trabalhador (2026-11-27): vários Rhinos no mesmo PC dividem a geração. `modo =
  trabalhador` no `opcoes`: não chama `/iniciar`, espera `estado` = gerando, confere os
  nomes do genoma (`/estado` → `parametros`), pega tarefas da mesma fila e, no fim,
  espera a próxima otimização até `parar`. Servidor: id das tarefas `rodada-geração-
  índice` (resposta de otimização antiga é ignorada), `POST /devolver` (trabalhador que
  para devolve as não feitas na hora; senão voltam em 120 s). `lote` padrão 16 → 4 (com
  16 e população 48, dois Rhinos ficavam 32/16). Testado sem Rhino (`test_devolver_e_
  rodada_antiga`). Esc é global (GetAsyncKeyState): para todos; o botão `parar` para um só.
- Conferido com dois Rhinos (2026-11-27, sala 48 × 20): 12,7 s contra 19,2 s só com o A
  (1,5×); A gerou 516, B 444. Perdas: espera de 0,1 s por consulta enquanto a GPU avalia
  (~65 ms/geração) → baixada para 0,02 s; o resto é a barreira de cada geração (todos
  esperam a GPU e o último lote). Na 1ª tentativa o B não entrou e não deixou pista
  (provavelmente servidor/código antigo ainda carregado); com o diagnóstico ("Trabalhador
  vê: …" e contador na mensagem) entrou normalmente. O log do componente agora se
  atualiza quando o laço escreve (ScheduleSolution → ExpireSolution).
- Etapa 4a (2026-11-27), módulo `termico` (`modulos/termico/`), sem pacote novo. Métricas:
  compacidade (1/m), area_envoltoria, volume, wwr e wwr_N/L/S/O (%), ganho_solar (kWh/ano),
  ganho_solar_piso (kWh/m²·ano), ganho_solar_N/L/S/O (kWh/m²·ano de vidro, 0 sem vidro),
  utci_conforto / utci_calor / utci_frio (% das 8760 h com UTCI 9–26, > 26, < 9 °C).
- Envoltória (`envoltoria.py`) = fachada + vidro + cobertura, normais para fora; pisos fora
  (os internos estragariam a conta). Volume pela divergência com (0, 0, z − z_base): faces
  verticais e a base dão zero, então a malha pode vir aberta embaixo; base que falta =
  Σ n_z·A (zero se a base já estiver na malha). Parede = normal entre 60° e 130° do zênite
  (como o Honeybee); orientação N 315–45°, L 45–135°, S 135–225°, O 225–315°. Validação
  analítica (caixa, caixa fechada elevada, rotação, WWR do protótipo).
- Ganho solar = radiação do motor A nos sensores do vidro (0,1 m para fora, grade/MF do
  nível do sol_luz, mesma matriz e cache) × FS constante (sem variação com o ângulo).
  `Variante.fator_solar` novo (padrão 0,42 = Generic Double Pane do Honeybee, τ 0,64);
  no protocolo `materiais → vidro → fs`. Validação: claraboia sem obstrução contra
  Σ (DHI + DNI·sen alt) do EPW, −0,8% (rápido), −0,2% (validação); vertical já validado
  na 1b (LB Incident Radiation).
- UTCI (`conforto_externo.py`): o método do LB Outdoor Solar MRT + LB UTCI Comfort.
  Pessoas = sensores nas faces de `entorno` voltadas para cima (grade do nível), ponto a
  0,918 m (o meio de uma pessoa de 1,8 m, como o LB Human to Sky Relation com 1 ponto).
  Exposição ao céu = patches de Tregenza vistos, ponderados pelo ângulo sólido (Σ = 2π,
  igual ao `dome_patch_weights` do LB); corpo ao sol = 0/1 por hora (sol no meio da hora).
  MRT por SolarCal: T_superfícies = T_ar, céu por IR horizontal (σ 5,6697e-8, ε 0,95),
  em pé, sol a 135°, α 0,7, ρ_piso 0,25, sem onda curta com sol < 2°. UTCI: polinômio de
  Bröde (210 termos) com os coeficientes extraídos exatamente da função do
  ladybug_comfort (`dados_conforto.json`, gerado por `scripts/gerar_ref_termico.py` com o
  Python do Ladybug Tools: o script passa "variáveis simbólicas" para a função); a cada hora
  vira um polinômio de grau 6 em ΔT = MRT − T_ar (Horner em float32 na GPU). Vento do EPW
  (10 m) limitado a 0,5–17 m/s. A tabela do fator de projeção do SolarCal (em pé, 135°)
  também vem do LB. Vento local (XLB) e temperaturas de superfície ficam para depois.
- Validação UTCI contra o ladybug_comfort (`testes/referencias/curitiba/termico_ladybug.json`):
  300 pontos sorteados, |dif| < 1e-9 °C; MRT e UTCI hora a hora com as entradas e altitudes
  do LB em três situações (céu 1/corpo 1; 0,5/0,5; 0,2/0): MRT < 1e-4 °C, UTCI < 1e-3 °C.
  Pegadinha: o leitor de EPW do Ladybug não alinha todos os campos do mesmo jeito (a
  temperatura vem deslocada uma hora, a radiação não), e o sol do LB está no início da
  hora; por isso o gabarito grava as séries que o LB usou. No módulo (sol no meio da hora),
  campo aberto em Curitiba: conforto 78,39% × LB 78,86%, calor 13,70 × 13,44, frio
  7,91 × 7,71. Céu e sol vistos conferidos contra a conta analítica de um muro de 1 km.
- Benchmark 4a (64 variantes com praça de 40 × 40 m, RTX 5070): geometria ~5000/s,
  ganho solar ~1300/s, UTCI 326/s (rápido, grade 2 m) e 105/s (padrão, grade 1 m).

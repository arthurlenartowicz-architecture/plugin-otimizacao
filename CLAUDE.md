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
- `otimizacao/`: GA ou CMA-ES (1 objetivo), NSGA-II (2), NSGA-III/RVEA (3 ou mais),
  coordenadas paralelas, clusters do Pareto
- `ia/`: modelos substitutos (PhysicsNeMo), usados só onde a física é cara
- `rhino/`: plugin C# (painel no Rhino, componentes do Grasshopper, leitura da
  geometria, visualização). Ver "Interface do usuário"
- `testes/`: validação de cada módulo

## Contrato de módulo
Cada módulo declara `nome`, `depende_de`, `metricas` (unidade e sentido
min/max) e implementa `avaliar(cena: CenaLote, cache) -> dict[str, Tensor]`,
com um valor por variante. Tudo roda em lote. Níveis de precisão:
`rapido` | `padrao` | `validacao`.
Um problema de otimização é uma lista de objetivos e restrições no formato
`"modulo.metrica"`, e o motor roda só os módulos necessários.

## Interface do usuário
Plano aprovado (2026-11-27). Referência: Jifto (painel direto no Rhino, uma aba
por análise, conector ao vivo com o GH). O que este plugin acrescenta: a
otimização. Três camadas, todas chamando a mesma biblioteca C# (a casca de
componentes do GH continua fina). O que já existe: componente Otimizar
(`Otimiza.gha`) e o Pareto (GhPython) = embrião das camadas 2 e 3.

1. **Painel no Rhino** (Eto.Forms, Rhino 8 e 9)
   - Aba **Cena**: botões "Selecionar entorno" e "Selecionar edifício
     analisado", arquivo EPW e localização. A seleção é só uma etiqueta no
     objeto (UserText) que vira o rótulo da `CenaLote` (entorno, contexto,
     fachada, vidro…): nunca apagar, mover nem alterar a geometria do usuário,
     e esconder uma layer não deve mudar a cena por acidente (problemas
     relatados no Jifto).
   - **Uma aba por módulo**: configurações, nível de precisão, botão
     "Calcular", legenda de cores sobre o modelo (DisplayConduit) e métricas
     resumidas.
2. **Componentes do Grasshopper** (os "cards", como Ladybug/Karamba)
   - "Entorno", "Edifício" e "Clima": alimentam a cena ao vivo, sem bake.
   - Um componente de análise por módulo (uma variante, resultado na hora).
   - Os parâmetros do edifício vêm de qualquer coisa do GH (sliders,
     Pufferfish, outros plugins de modelagem). Um componente "Genes" diz ao
     otimizador quais sliders/Value Lists ele pode mexer (hoje o genoma é
     lido direto das entradas do Otimizar).
3. **Aba de Otimização**
   - Lista de todas as métricas registradas pelos módulos: o usuário marca
     1 ou várias, escolhe min/max e define restrições (vira o Problema em
     texto do protocolo).
   - Algoritmo automático pelo número de objetivos: 1 → GA ou CMA-ES (falta
     implementar); 2 → NSGA-II; 3+ → NSGA-III/RVEA.
   - Gráficos ao vivo: convergência por geração (hipervolume / melhor valor),
     frente de Pareto 2D/3D, coordenadas paralelas, clusters (com 1 objetivo:
     só a curva do melhor).
   - Clicar numa solução aplica os valores nos sliders e mostra a geometria
     no Rhino (o `aplicar` do Pareto já faz isso).

### Geração de variantes (dois modos)
- **Via Grasshopper** (já implementado na 3b/4c): o otimizador pede uma
  geração inteira, o GH gera uma a uma e a GPU avalia todas juntas. Flexível;
  o gargalo é o recálculo do GH (~13 ms/variante na sala de exemplo).
- **Parâmetros nativos** (a fazer): formas simples (altura, rotação, recuos,
  janelas, brises, treliças) geradas pelo próprio motor, sem passar pelo GH
  (base: `prototipos.predio_de`, `estrutura.prototipos`). Bem mais rápido.

## Stack
Python 3 · PyTorch (CUDA 12.8+) · NVIDIA Warp · XLB (vento/LBM) ·
PhysicsNeMo (IA) · pymoo (otimização) · C# / RhinoCommon / Eto.Forms (plugin)

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
    Ordem revista (2026-11-27, plano da 4f): 4f1 → 4f2 → 4d → 4e → 4f3 → 4f4 → 4f5 (4d e 4e
    antes dos componentes e do painel, porque devem trazer rótulos/materiais e genes novos).
  - [x] 4a · térmico 🟢: compacidade, WWR, ganho solar, UTCI (módulo `termico`)
  - [x] 4b · térmico 🟡: modelo RC multicamadas em lote (cargas, EUI, balanço zero, PPD,
    conforto adaptativo), validado contra o EnergyPlus
  - [x] 4c · plugin C# (`rhino/plugin/`, Otimiza.gha): componente Otimizar no lugar do
    `otimizar_gh.py`, mesmo protocolo v1; testado contra o servidor real e no Rhino 8
  - [ ] 4f · interface do usuário (seção "Interface do usuário"): painel no Rhino com
    aba Cena e aba por módulo, componentes GH por módulo + Genes, aba de Otimização
    (escolha de 1 ou mais objetivos, GA/CMA-ES para 1, gráficos ao vivo), parâmetros nativos
    - Plano aprovado (2026-11-27). Cada subetapa: entrega · validação · conferência no Rhino.
    - [x] 4f1 · 1 objetivo: `auto` 1 → GA, 2 → NSGA-II, 3+ → NSGA-III; `algoritmo = cmaes`
      (pacote `cma` 4.6.0 já instalado) só com todos os parâmetros contínuos (num categórico o
      CMA-ES supõe uma ordem que não existe). Com 1 objetivo a "frente" = o melhor projeto.
      Validação: esfera 10D (perto de 0 num orçamento fixo); Rastrigin 2D (ótimo global) e
      5D (melhor que a busca aleatória com o mesmo orçamento), GA e CMA-ES; treliça Pratt
      com 1 objetivo (massa, utilização ≤ 100%) = ótimo da busca exaustiva (735 projetos)
      ou a < 1%. Rhino: sala com `max sol_luz.da`, `aplicar` leva ao melhor.
    - [x] 4f2 · Janela de Otimização (Eto), aberta pelo Otimizar: métricas do `GET /metricas`
      (unidade, sentido), 1 ou mais objetivos, min/max, restrições, nível, população,
      gerações; problema guardado no componente (salvo no .gh), entrada `problema` em texto
      sobrescreve. Gráficos num WebView com plotly.js (cópia local, versão fixa, sem
      internet; ponte JS ↔ C# para o clique): convergência (hipervolume; melhor valor com 1
      objetivo), Pareto 2D/3D, coordenadas paralelas, clusters (conta no `pareto.py`).
      Clique → `aplicar` nos sliders. Protocolo: `GET /progresso` novo (histórico por geração
      + frente atual), só adição, versão continua 1. Código em `Otimiza.UI` (sem depender do
      GH, para o painel da 4f4). `pareto.gh` segue valendo até a janela ser aprovada.
      Validação (`Otimiza.Testes`, lógica fora da tela): texto do problema montado = esperado;
      16 × 4 contra o servidor real: hipervolume do arquivo não decresce (referência fixa),
      frente final = `resultado.json`; solução → valores dos sliders. Rhino: sala 48 × 20,
      gráficos andando por geração, clicar em 3 pontos e conferir sliders/geometria; mesma
      solução na janela e no `pareto.gh`.
    - [x] 4d · módulo `carbono_custo`: carbono incorporado (A1–A3) e operacional, total em N
      anos, custo de construção, operação e ciclo de vida; tabela `fatores.json` editável
      (painel `fatores =` ou campo `carbono_custo` do protocolo). Entra na janela pelo `/metricas`.
    - [x] 4e · módulo `programa`: índices urbanísticos (área construída, CA, taxa de ocupação,
      altura, recuo mínimo) com o lote, e programa de necessidades (atendimento e desvio das
      áreas-meta, eficiência, adjacências, distância ponderada) com pisos nomeados. Entrada =
      `programa.json` (ou campo `programa`); componentes Lote, Programa e Ambiente no Otimiza.gha
      e entrada `programa` no Otimizar (adiantados da 4f3). Aba visual do programa fica para a 4f4.
    - [ ] 4f3 · Componentes do GH: Clima (EPW, local), Entorno e Edifício (geometria + rótulo
      → objeto Cena), Genes (sliders/Value Lists ligados = genoma), um de análise por módulo
      (`/avaliar` na hora). Otimizar aceita Cena + Genes e mantém as entradas atuais.
      4f3b · campos por sensor: `Modulo.campos(...)` no contrato (sensores + valores: radiação,
      DA, horas de sol, UTCI) e opção no `/avaliar`; malha colorida como no Ladybug.
      Validação: sala montada com os componentes = Python (`predio_de`) no nível validação,
      tolerâncias do `test_gh.py`, mesmo genoma do Otimizar atual; Σ campo × área = métrica.
      Rhino: mexer num slider, métrica e cores mudam na hora.
    - [ ] 4f4 · Painel no Rhino (`.rhp`): aba Cena (UserText `otimiza.rotulo`, nunca altera
      geometria; cena lida pela etiqueta, não pela visibilidade da layer; EPW), aba por
      módulo (ajustes, nível, Calcular, DisplayConduit com legenda pelos campos, métricas),
      janela da 4f2 como aba Otimização. Biblioteca comum (Cliente + UI) passa ao `.rhp`; o
      `.gha` depende dele (resolve a DLL ao lado do `.gha` carregado por bytes). Validação:
      selecionar/calcular/limpar não mudam a geometria (hash de vértices e atributos, fora
      o UserText); esconder/trancar layer não muda a cena; métricas = componente da 4f3.
      Rhino: marcar, Ctrl+Z, esconder layers, calcular, ver a legenda.
    - [ ] 4f5 · Parâmetros nativos: `/iniciar` com `gerador` ("predio", "trelica") e faixas;
      o servidor gera e avalia sem o GH; Rhino só mostra (DisplayConduit; bake em layer nova
      só se o usuário pedir); "Modo: GH | nativo" na janela. Validação: nativo = 
      `otimizar_problema` direto (mesma semente); mesmo genoma → mesma geometria nos dois
      modos. Medir: sala 48 × 20 via GH 20,3 s × nativo (previsto ~3 s).
  - Objetivos do módulo `termico` (🟢 = direto na GPU; 🟡 = modelo simplificado):
    - [x] carga de resfriamento e aquecimento, kWh/ano 🟡 (modelo RC em lote
      por zona; ganhos solares vêm do `sol_luz`)
    - [x] consumo energético total, EUI kWh/m²·ano 🟡 (cargas ÷ COP/eficiência
      + iluminação usando a autonomia de luz natural do `sol_luz` + equipamentos)
    - [x] conforto interno 🟡: graus-hora / horas de desconforto, conforto
      adaptativo (ASHRAE 55 / EN 16798), PMV/PPD
    - [x] conforto externo, UTCI 🟢 sem o vento: temperatura radiante média a
      partir da radiação do `sol_luz` (motor A) + ar, umidade e vento do EPW.
      Era parte da Etapa 1 ("conforto externo") e não foi feito lá
    - [x] ganho solar pelas aberturas, W/m² e WWR por orientação 🟢 (radiação
      nos vidros × τ/FS; WWR direto da geometria)
    - [x] compacidade, superfície ÷ volume 🟢 (só geometria)
    - [x] balanço energético zero, geração − consumo kWh/ano 🟡 (depende de
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
- Etapa 4b (2026-11-27), modelo térmico (`modulos/termico/rc.py`, `zona.py`,
  `conforto_interno.py`). Primeiro tentei a 5R1C da ISO 13790: com uma massa só não dava
  conta de parede leve por dentro + forro + laje pesada (aquecimento 2–3× o do E+, sala livre
  1 °C mais fria, oscilação errada) — trocada por um RC multicamadas no espírito da ISO
  52016-1: uma zona por variante; cada elemento (parede, cobertura, piso no solo, piso
  interno/adiabático, vidro) com as camadas reais em nós de ≤ 4 cm + faces sem massa; nós de
  ar e radiante (estrela, h_r ≈ 5,2). Externo: h_e = 4 + 4·v_local + h_r (vento médio do
  ano na altura, perfil do E+ para o terreno, padrão "cidade" como o HB), sol α·I e onda
  longa com o céu F_r·h_r·(θ_céu − θ_e) (F_r = F_céu·β do E+: parede 0,35, cobertura 1; h_r
  hora a hora na média céu–ar). Interno: convecção típica do TARP (parede e piso 2,0; teto
  1,5 W/m²K). Solo: face externa do piso na temperatura do solo do cabeçalho do EPW (0,5 m,
  mensal; `Clima.temp_solo` novo). Vidro: um nó (U = 1,688 com películas). Sol pelos vidros
  (FS × I × fator angular de vidro duplo por Fresnel): transmitido τ = 0,84·FS (razão do
  Generic Double Pane), o direto cai no piso (α 0,8) e o difuso + refletido vão às faces
  por área × absortância (gesso 0,5, forro 0,2, concreto 0,8), como o E+; o absorvido no
  vidro (FS − τ) entra pela face interna da janela. Infiltração ρ·c·vazão (ρ médio pela T e
  pressão do EPW). Climatização ideal no ar, sem limite. Euler implícito de 1 h; a matriz é
  constante, então vai para coordenadas modais (√D·M⁻¹·√D simétrica → `eigh`, exato, sem
  truncar): cada modo evolui sozinho num kernel Warp (uma thread por variante, variante no
  último eixo); o ano roda 2× (a 1ª aquece). 36 nós.
- Radiação horária nas superfícies: `motor_a.ceu_direcional_horario` (céu + solo
  lambertiano por hora; `componente="direta"` só o sol) e `irradiancia_agregada` (Σ área·I
  por variante × tipo antes de multiplicar pelas horas: o custo não cresce com sensores ×
  horas).
- Programa padrão (`rc.Programa`, = caso de validação): pessoas 0,0565/m² × 120 W sensível
  (30% radiante), iluminação 10 W/m² (57% radiante), equipamentos 10 W/m² (50%), seg–sex 8–18 h
  (ano 2017; iluminação/equipamentos 5% fora do horário), infiltração 0,5 trocas/h, termostato
  20/24 °C sempre, COP 3/3. Construções = genéricas do HB (camadas em `CONSTRUCOES`).
  EUI = (aquec/COP + resfr/COP + iluminação + equipamentos)/área de piso; a iluminação desliga
  nas horas ocupadas na fração do `sol_luz.da` médio (vale também como ganho interno).
  balanco_zero = sol_luz.fv − consumo.
- Contrato: `Modulo.precisa(metricas)` novo (métricas das dependências que as pedidas usam;
  None = todas, o comportamento antigo). O motor só roda dependências necessárias: pedir
  `termico.compacidade` não roda o sol_luz; as métricas RC pedem só `sol_luz.da` (e `fv`).
- Conforto interno: adaptativo ASHRAE 55 80% como o padrão do LB (prevalente = média mensal
  externa limitada a 10–33,5; neutra 0,31·T + 17,8; ±3,5 °C), em evolução livre, horas
  ocupadas: `conforto_adaptativo` (%), `graus_hora_calor`/`frio` (°C·h). PPD de Fanger (a
  iteração do ladybug_comfort, em lote) no climatizado, horas ocupadas, v 0,1 m/s, met 1,1,
  roupa de Schiavon pela temperatura das 6 h, UR interna com a pressão de vapor externa.
  EN 16798 ficou para depois.
- Validação 4b (`scripts/gerar_ref_rc.py` com o Python do Ladybug Tools: HB Model → IDF →
  EnergyPlus do OpenStudio 3.10, sem Rhino; gabarito `testes/referencias/rc/eplus_rc.json`).
  Sala 6×8×3 m, Curitiba, cargas ideais 20/24 °C. A (janela N 40%, piso adiabático):
  aquecimento 252 × E+ 229 kWh (+10%), resfriamento 2437 × 2407 (+1,3%), total +2,0%.
  B (N 40% + O 30%, piso no solo com a temperatura do solo do EPW): aquecimento 361 × 302
  (+19%), resfriamento 2158 × 2180 (−1,0%), total +1,5%. Evolução livre (operativa hora a
  hora): viés −0,17 / −0,28 °C, rms 0,49 / 0,45 °C, amplitude diária 5,94 × 6,07 /
  5,40 × 5,38. Conforto adaptativo (ocupadas) 27,6 × 27,5% / 44,6 × 41,7%. Critérios dos
  testes: resfriamento e total ±10%; aquecimento ±25% e ≤ 3% da carga total (é ~10% da carga
  em Curitiba; o plano previa ±15% em todas — o aquecimento da B fica acima, decisão a rever
  se o TCC usar clima frio); livre viés < 0,4 e rms < 0,8 °C; conforto ±5 pontos.
  Adaptativo e PMV/PPD contra o ladybug_comfort com as mesmas entradas: iguais (PMV < 1e-6).
  O ladybug.sql já converte J → kWh. O HB precisa de `heating_limit=no_limit` e sizing
  desligado (sem dias de projeto).
- Benchmark 4b (RTX 5070, rápido): 64 variantes, cargas + EUI ~120/s; tudo do RC (cargas,
  EUI, balanço, PPD, adaptativo: duas simulações) ~70/s; com 256 variantes ~200/s e ~110/s
  (a simulação de um ano leva ~0,3 s para qualquer lote: o custo é a sequência de horas).
- Etapa 4c (2026-11-27), plugin C# (`rhino/plugin/`, ver `rhino/plugin/LEIAME.md`): .NET 8 SDK
  8.0.428 instalado (winget); sem NuGet (HTTP e System.Text.Json do próprio .NET). Alvo só
  net8.0(-windows): o Rhino 8.37 roda no .NET 8 (só há os runtimes 8 e 10; nada de net48/net7).
  `Otimiza.Cliente` (sem Rhino): `ClienteProtocolo` (HttpClient síncrono, sem proxy, conexão
  mantida; JSON sem escapar o '+' do base64), `Codificacao` (float32/int32 → base64, como o
  GhPython), `Opcoes` (painel chave = valor) e `Ciclo` = a classe Laco do `otimizar_gh.py`
  (conectar/iniciar, tarefas, resultados, trabalhador com conferência dos nomes, `/devolver`,
  parar), com `gerar` e eventos (AoEscrever/AoTerminar/AoEsperar) fornecidos por quem usa.
  `Otimiza.GH` → `Otimiza.gha` compila os arquivos do Cliente junto (um arquivo só: evita
  problema de dependência ao lado do .gha carregado por bytes); RhinoCommon, Grasshopper,
  GH_IO e Eto do Rhino instalado com Private=false. Componente com as mesmas entradas/saídas
  (GUID c3a1f6d2-…); genoma, captura no SolveInstance, timer Eto de 5 ms com blocos de
  0,25 s, Esc, vista sem redraw, sliders restaurados e Pareto recarregado no fim;
  `info.cliente.componente` = "Otimiza.gha <versão>". Pasta do projeto: sobe do .gha (ou do
  .gh) até o CLAUDE.md; `raiz =` no `opcoes` força. Carregado pelo `GrasshopperDeveloperSettings`
  (pasta `rhino/plugin/bin`, fora do git); `montar_otimizar_cs.py` monta `otimizar_cs.gh`
  (o `montar_otimizar.py` com `_USAR_CS`). O `otimizar_gh.py` continua valendo.
- Teste 4c (`testes/test_cliente_cs.py`, pula sem SDK ou EPW): compila e roda
  `rhino/plugin/Otimiza.Testes` (console no papel do Rhino) contra o servidor real: base64
  igual ao do Python; `/avaliar` de caixas em mm com contexto em `fixas` = conta analítica
  (volume, área, compacidade, WWR, rel 1e-5); otimização 16 × 4 com 1 principal + 2
  trabalhadores (um para no meio e devolve tarefas, o outro volta a esperar), erro do cliente
  → inviável, trabalhador com genoma diferente recusado. ~5 s.
- Conferido no Rhino 8 (2026-11-27, sala de exemplo, DA × UDI × ASE, rápido, NSGA-III 48 × 20):
  960 avaliações, 957 geradas; GH 12,7 ms/variante (3b: 15,8; −20%: só a captura/conversão
  saiu do IronPython, a sala ainda é um GhPython), GPU 2,9 ms/variante (3b: 1,3), total
  20,3 s (3b: 19,2 s). O ganho no GH foi comido pela GPU e pela espera entre gerações; o
  gargalo segue sendo o recálculo do GH (para acelerar: vários Rhinos, ou a geometria do
  exemplo em C#).
- Interface do usuário (2026-11-27): três camadas (painel no Rhino estilo Jifto,
  componentes do GH, aba de Otimização), descritas na seção "Interface do usuário";
  entra como 4f. Otimização com 1 objetivo usa GA/CMA-ES. Dois modos de gerar
  variantes: via GH (o atual) e parâmetros nativos gerados pelo motor. Interface em
  Eto.Forms. Marcar entorno/edifício é só etiqueta: nunca altera a geometria.
- 4f1 (2026-11-27): `auto` 1 objetivo → GA (`pymoo` GA: mesmos SBX + mutação polinomial e
  reparo dos discretos do NSGA-II; torneio e sobrevivência "viável primeiro, depois menor F").
  `algoritmo = cmaes` (pymoo CMAES sobre o `cma` 4.6.0): recusa inteiro/categórico; lote =
  população (1º lote = amostra LHS do mesmo tamanho, x0 = o melhor), sigma 0,25 do intervalo,
  tolfun/tolx 1e-11 (pode parar antes das gerações pedidas). O CMAES do pymoo ignora o Repair e
  põe os inviáveis em inf (se todos forem inviáveis ele fica cego): no nosso problema o CMA-ES
  vê F = 1e13 + violação nos inviáveis, sem restrição para o pymoo (só a ordem importa, então
  equivale ao "viável primeiro" do GA); o registro guarda F e violação reais. ga/cmaes com 2+
  objetivos → erro. `Resultado.melhores(n)`: n melhores viáveis distintos; com 1 objetivo o
  `exportar` grava os 20 melhores (a frente seria só o melhor), em ordem.
- Validação 4f1 (`testes/test_mono_objetivo.py`, ótimos deslocados para x* = 1,234 em
  [−5,12; 5,12]): esfera 10D (100 × 200) GA f = 1,4e-5, CMA-ES 5e-14 (13 mil avaliações, parou
  pela tolerância); Rastrigin 2D os dois no ótimo global; 5D (100 × 100, 3 sementes) GA
  ≤ 0,001 e CMA-ES 0, aleatória com o mesmo orçamento 17,1; restrição ativa (x0 ≥ 3) GA
  +0,03% de f*, CMA-ES exato, ambos viáveis. Treliça Pratt, massa com utilização ≤ 100% e
  flecha ≤ L/250 (4,8 cm): GA 400 avaliações 143,5 kg × busca exaustiva (735) 143,2 kg (+0,2%).
  Servidor com 1 objetivo = `otimizar_problema` direto (mesma semente; `test_servidor`).
  Conferência no Rhino: `rhino/grasshopper/conferir_1objetivo.py` (monta a definição com
  Otimiza.gha, `max sol_luz.da`, caso `sala_1obj`, salva em `resultados/`) e
  `scripts/conferir_1objetivo.py` (lê o resultado.json, reavalia pelo protótipo, compara com a
  busca aleatória e com a otimização de 3 objetivos).
- Conferido no Rhino 8 (2026-11-27, Otimiza.gha, sala de exemplo, `max sol_luz.da`, rápido,
  48 × 20): algoritmo ga, 960 avaliações (937 geradas no GH), 28,3 s; 20 soluções distintas em
  ordem; melhor = todas as janelas 80% (rotação −45°), DA 93,79. Reavaliadas pelo protótipo em
  Python: |dif| máx 0,03 ponto (já com a semente por variante). Busca aleatória com o mesmo
  orçamento 93,69; maior DA da otimização de 3 objetivos (sala_gh) 93,62. O DA máximo da sala é
  um platô fácil (as 20 melhores têm DA 93,79): serve para conferir o fluxo, não para mostrar
  vantagem do GA (isso está nos testes: Rastrigin, treliça).
- Semente dos raios (achado na 4f1): o DA de um projeto dependia dos vizinhos de lote (a
  semente seguia a posição no lote). 192 salas, lote 192 × lotes de 48: rápido |dif| média 0,09,
  p95 0,30, máx 1,6 pontos; padrão 0,02 / 0,05 / 0,56. Corrigido: motores B e C usam como
  semente a posição do sensor/pixel dentro da própria variante (`Cena._indice_local`); a mesma
  variante dá o mesmo valor em qualquer lote, e variantes diferentes usam os mesmos números
  aleatórios por sensor (comparar projetos tem menos ruído). Com uma variante só nada muda
  (índice local = índice antigo): validações 1c/1d/1e iguais. Teste: `test_semente_lote.py`
  (sala sozinha, no início, no meio e no fim do lote; falhava com o código antigo).
  Na sala de exemplo o DA máximo é fácil (janelas grandes): GA 93,8 × busca aleatória 93,7.
- O CMA-ES do pymoo não é reprodutível entre processos/ordens de teste (depende de estado
  global de números aleatórios): no Rastrigin 5D a semente 0 deu 0 sozinha e 1,99 na suíte.
  O `test_sessao_igual_ao_otimizar` (mesmo processo) continua idêntico.
- 4f2 (2026-11-27), servidor: `otimizacao/progresso.py` (`Progresso.registrar` por lote, com
  os mesmos F/violação do registro da Sessao): histórico por geração (avaliações, viáveis,
  n na frente, melhor por objetivo no sentido original, hipervolume) e a frente atual (=
  `Resultado.frente()`; 1 objetivo = `melhores(20)`), até 300 pontos com clusters, refeita só
  quando chega geração nova. Hipervolume com normalização FIXA (ideal/nadir dos viáveis da 1ª
  geração com algum, referência 1,1): nunca diminui; até 3 objetivos exato (pymoo), 4+ Monte
  Carlo com 20 mil amostras fixas em [−1; 1,1]^m (também nunca diminui). `GET /progresso
  [?desde=g]` = `/estado` + `parametros_espaco` + histórico + frente (só acrescentado ao v1).
  Testes: `test_progresso.py` (ZDT1: frente = a do Resultado em toda geração, HV igual ao
  calculado à parte; DTLZ2 4 obj.; 1 objetivo; inviáveis/NaN) e `test_servidor` (rota HTTP).
- 4f2, janela: `rhino/plugin/Otimiza.UI/JanelaOtimizacao.cs` (Eto Form, sem Grasshopper;
  quem abre implementa `IAnfitriao`) + modelo sem tela em `Otimiza.Cliente/Configuracao.cs`
  (`Catalogo` do `/metricas`, `Configuracao` → problema em texto, JSON salvo no .gh,
  `DeProblema` lê o painel, `DadosGraficos.DeResultadoJson`). Gráficos num WebView (Eto 2.11 do
  Rhino 8 = WebView2) com `rhino/plugin/web/janela.html` + `graficos.js` + plotly.js 2.35.2
  local (versionado, MIT, 4,56 MB, SHA-256 conferido com o do jsdelivr). C# → JS:
  `ExecuteScriptAsync("otimiza.atualizar(json)")` só quando os dados mudam (consulta a cada
  0,5 s; servidor fora do ar: tenta a cada 3 s com timeout de 0,4 s para não travar). JS → C#:
  `window.eto.postMessage` (ou o título da página, `DocumentTitleChanged`). Componente:
  duplo clique/menu abre a janela; config em `Write/Read` (chave `otimiza.janela`); painel
  `problema` ligado manda; painel `opcoes` sobrescreve nivel/populacao/geracoes/algoritmo
  (linhas da janela entram antes); Iniciar da janela = `pedidoIniciar` + `ExpireSolution`;
  Aplicar = `Genoma.Aplicar` num `ScheduleSolution`. `.gha` passa a referenciar Rhino.UI
  (`RhinoEtoApp.MainWindow` como dono da janela) e copia `web/` para `bin/web/`.
  Página conferida no navegador do app com dados reais (treliça 1 e 2 obj., DTLZ2 3 obj.):
  achou e corrigiu gráfico invadindo a tabela (altura fixa de 450 px do plotly) e eixo z
  preso ao trocar de 2 para 3 objetivos. Testes C#: `test_cliente_cs.py` (catálogo, problema
  da janela usado na otimização de verdade, ida e volta do JSON, painel → janela, erros,
  painel opcoes manda, `/progresso` e `resultado.json` convertido = mesma frente).
  Conferência no Rhino: `rhino/grasshopper/conferir_janela.py`.
- Conferido no Rhino 8 (2026-11-27, Otimiza.gha, sala de exemplo sem painel `problema`): a
  janela abre no duplo clique, lista as métricas do servidor, e os objetivos marcados nela
  (DA max, ASE min, UDI max) foram os da otimização (auto → NSGA-III, 48 × 20, rápido): 960
  avaliações em 21,5 s (GH 14,7 ms/variante, GPU 1,4 ms). Gráficos no WebView2 do Rhino
  atualizando por geração (convergência, Pareto 2D/3D, coordenadas paralelas); Aplicar levou a
  solução aos sliders e à sala no Rhino; objetivos voltam ao reabrir o .gh salvo. Sem erros.
- 4d (2026-11-28), módulo `carbono_custo` (`modulos/carbono_custo/`, sem pacote novo). Métricas:
  carbono_incorporado (kgCO2e) e _m2 (÷ área de piso), carbono_operacional (kgCO2e/ano =
  `termico.consumo` × fator da rede), carbono_total (incorporado + N × operacional, sem desconto),
  custo_construcao (R$) e _m2, custo_operacional (R$/ano = consumo × tarifa), custo_ciclo_vida
  (construção + operação a valor presente, Σ 1/(1+i)^t). Sem GPU: os três níveis dão o mesmo valor.
  Áreas = as do RC (`zona.geometria_rc`): parede = fachada, vidro, cobertura, piso na base = piso no
  solo, pisos acima = lajes (1×; o RC conta 2× por ter massa dos dois lados). Faces interno/contexto/
  entorno e esquadrias não entram. Estrutura: massa das barras (`Estrutura.massa`, a mesma do
  módulo estrutura) × fator do aço. `precisa`: só as métricas de operação pedem `termico.consumo`
  (incorporado e construção não rodam termico nem sol_luz e não precisam de EPW no servidor).
- Fatores (`fatores.json`, versionado, fonte por item): camadas = as `CONSTRUCOES` do `rc.py`
  [material, espessura, densidade] (teste confere), vidro duplo 2 × 6 mm. Carbono ICE v3.0 (2019,
  Bath/Circular Ecology): tijolo 0,213; concreto leve (aircrete) 0,28; concreto geral 0,103; EPS
  3,29; gesso 0,39; manta (betume) 0,43; forro ≈ lã mineral 1,28; vidro 1,44; aço perfil 1,55
  kgCO2e/kg. Conferidos em fonte secundária só aço 1,55 e gesso 0,39 (não achei a planilha do ICE
  aberta; a versão 4.x mudou alguns, ex.: vidro float ~0,85): conferir na planilha antes do texto
  final do TCC. SIDAC (valores brasileiros) não comparado ainda. Rede: 0,0385 kgCO2/kWh = MCTI, SIN
  2023 (desde 2025 o MCTI usa outra base, fatores mensais no SIRENE). Custos (R$/m² parede 250,
  cobertura 350, piso solo 200, laje 300, vidro 1200, aço R$ 18/kg) e tarifa R$ 0,85/kWh são ORDEM DE
  GRANDEZA, não conferidos: calibrar com SINAPI-PR e a tarifa da Copel. N = 50 anos, i = 4% real.
- Trocas do usuário: `fatores_de(opcoes)` = padrão ← `arquivo` (cópia editada) ← resto do dicionário;
  chave desconhecida = erro (material novo só em `materiais`). Chegam ao módulo por `CenaLote.opcoes`
  (campo novo, `{"carbono_custo": {...}}`; o cache não serve: é compartilhado entre otimizações).
  Protocolo: campo opcional `carbono_custo` no `/iniciar` e `/avaliar` (só acrescentado, versão 1);
  painel `opcoes` do Otimizar: `fatores = caminho.json` → `{"arquivo": caminho}` (GhPython e C#).
- Validação 4d (`testes/test_carbono_custo.py`): conta à mão com os números da tabela escritos no
  teste (sala 6×8×3, janela N 40%: parede 76,8 m² × 87,77, vidro 7,2 × 43,2, cobertura 48 × 57,15,
  piso 48 × 53,22 kgCO2e/m²; laje a meia altura +48 × 45,26), treliça Pratt = massa do módulo
  estrutura (validada contra o Karamba) × 1,55 e × R$ 18, operação e valor presente (anuidade
  50 anos a 4% = 21,4822), trocas por arquivo/dicionário e erros, `precisa` sem termico, operacional
  pelo motor com o termico de verdade, servidor (`/metricas`, campo `carbono_custo`, sem EPW).
  Tolerância 1e-9 relativa (aritmética). Comparação com EC3/One Click LCA não feita: serviços online
  com conta, fora do ambiente.
- 4e (2026-11-28), módulo `programa` (`modulos/programa/`, sem pacote novo, sem raios nem clima:
  `programa.*` não carrega EPW). Métricas: area_construida (m², Σ pisos = a área de piso do
  termico), ca (área construída ÷ lote), taxa_ocupacao (%), altura (m, z máx. do projeto − cota do
  lote; sem z no lote, a base do projeto), recuo_min (m, menor distância horizontal à divisa;
  negativa = −a maior distância para fora), atendimento_programa (Σ mín(área, meta) ÷ Σ metas),
  desvio_programa (Σ |área − meta| ÷ Σ metas), eficiencia_planta (ambientes do programa ÷ área
  construída; pisos sem nome = circulação), adjacencia (% do peso dos pares atendidos),
  distancia_ponderada (média ponderada da menor distância entre centros dos ambientes dos dois
  usos). Projeto = fachada/vidro/cobertura/piso/interno.
- Taxa de ocupação por grade (centro da célula dentro de algum triângulo projetado, na GPU;
  células 0,5 / 0,25 / 0,1 m em rápido / padrão / validação): paredes verticais não projetam, a
  projeção vem de pisos e coberturas (balanço conta). Caixa girada (0–87°): erro médio 0,49 /
  0,16 / 0,03%, máx. 2,1 / 1,0 / 0,08%. ~240 variantes/s com todas as métricas urbanísticas (o
  laço por variante em Python domina). Recuo exato (vértices × divisas e cantos do lote × arestas).
- Adjacência = bordas dos pisos (arestas de um triângulo só, vértices soldados a 1 mm: emendas e
  diagonais não contam) paralelas (±5°), a ≤ `parede_max` (0,4 m) e na mesma cota (±0,5 m),
  sobrepostas por ≥ `comprimento_min` (0,8 m, cabe uma porta). Uso que falta: par não atendido e
  fora da distância (sem nenhum par medível, NaN = inviável).
- Entrada: `programa.json` (`necessidades.py`: lote, areas, adjacencias em lista ou texto
  "a-b:peso", adjacencia; `arquivo` ← resto do dicionário, chaves de cima trocam inteiras e
  `adjacencia` combina; chave desconhecida = erro). Chega ao módulo por
  `CenaLote.opcoes["programa"]`; protocolo: campo opcional `programa` no `/iniciar` e `/avaliar`
  (v1, só acrescentado), com erro já no pedido se faltar lote/áreas/adjacências para as métricas
  pedidas. Ambientes: item de malha de `piso` com `"ambiente": nome` → `Variante.ambientes`
  (índice por face, −1 = nenhum) + `usos` (campos novos, opcionais); `malhas_de_variante` devolve
  um item por ambiente (ida e volta igual).
- GH (decisão do usuário, 2026-11-28: JSON agora + componentes; aba visual na 4f4): Lote (curva
  fechada e plana do Rhino ou do GH → JSON em metros), Programa (usos + áreas + adjacências →
  JSON + resumo) e Ambiente (piso + nome → `AmbienteGoo`, com o nome desenhado no Rhino; o
  Otimizar recebe o objeto `Ambiente` pelo `ScriptVariable`, C# e GhPython). Otimizar C#: entrada
  `programa` acrescentada no FIM (para .gh antigos abrirem com as entradas no lugar; conferir no
  Rhino) e `necessidades = arquivo.json` no painel opcoes. Lógica sem Rhino em
  `Otimiza.Cliente/ProgramaJson.cs`. O `otimizar.gh` (GhPython) não foi remontado.
- Validação 4e (`testes/test_programa.py`, conta à mão): caixa 6×8×3 num lote 20×30 (CA 0,08, TO
  8% exata com a grade alinhada, altura 3 / 4 com o lote em z −1, recuo 5); girada 30° (TO pela
  grade: tolerância 3 / 1 / 0,4% por nível; recuo pelos cantos, 1e-9); balanço de 2 m (TO 64/600,
  recuo 3) e caixa fora do lote (−2); planta com sala (dois retângulos), cozinha e quarto a 15 cm e
  circulação sem nome: áreas, atendimento, desvio, eficiência, adjacência 60% (limites trocados: 0
  e 40%), distância ponderada; lote = individual; protocolo (ida e volta; 'ambiente' fora do piso
  = erro) e servidor sem EPW. C# (`test_cliente_cs.py`): JSON do Lote/Programa/Juntar, 8 erros
  esperados, `necessidades`, `/avaliar` com pisos nomeados em mm = conta à mão. Não há ferramenta
  de referência (como o Ladybug); conta analítica, pela regra do projeto.
- `test_rastrigin_2d_global[cmaes]` falha ao rodar `test_mono_objetivo.py` sozinho (também no
  commit 37e417a, antes da 4e) e passa na suíte inteira: é a não reprodutibilidade do CMA-ES do
  pymoo já registrada acima.

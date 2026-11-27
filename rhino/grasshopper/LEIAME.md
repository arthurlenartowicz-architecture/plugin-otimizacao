# Exportar referências do Ladybug

`exportar_referencias.py` grava em `testes/referencias/<caso>/` a geometria, os
sensores, o sol e o céu do Ladybug (e, depois, os resultados do Ladybug), para
os testes do motor compararem.

## Montagem automática (recomendado)

Na linha de comando do Rhino 8:

```
_-RunPythonScript "C:\Users\PC\Documents\plugin-otimizacao\rhino\grasshopper\montar_definicao.py"
```

O script abre o Grasshopper, monta a definição inteira (componente GhPython
com o código, painéis preenchidos, botão, log) e salva
`exportar_referencias.gh`. Nas próximas vezes, abra só o `.gh`. Se mudar o
`exportar_referencias.py`, rode o script de novo para atualizar o código.

## Montagem manual

1. Coloque um componente **Script** (Python 3) ou **GhPython** e cole o código.
2. Crie as entradas `pasta`, `caso`, `geometria`, `contexto`, `pontos`,
   `normais`, `epw`, `resultados`, `nome_resultado`, `exportar`. Marque como
   **List Access** as entradas `geometria`, `contexto`, `pontos`, `normais` e
   `resultados`.
3. Renomeie a saída para `log` e ligue um Panel.
4. Em `pasta`, um Panel com o caminho completo de `testes\referencias`.
5. Ligue um **Button** em `exportar` e clique.

Para os primeiros testes (sol e ordem do céu) bastam `pasta`, `caso`, `epw`
e `exportar`.

## Depois de exportar

```powershell
.venv\Scripts\python.exe -m pytest testes\test_ladybug.py -v -s
```

## Karamba (Etapa 2)

Definição separada e pequena (não mexe na do Ladybug/Honeybee). Uma vez, no Rhino 8:

```
_-RunPythonScript "C:\Users\PC\Documents\plugin-otimizacao\rhino\grasshopper\montar_karamba.py"
```

Cria `validar_karamba.gh` com um único componente GhPython ("Validar Karamba",
código de `karamba_validar.py`), um botão `rodar` e um painel de log. Nenhum
componente do Karamba vai para o canvas e nada roda na montagem. Ao apertar
`rodar`, o componente lê cada `testes/referencias/karamba/entrada_<caso>.json`,
monta e analisa o modelo pela API do Karamba (KarambaCommon.Toolkit) e grava
`karamba_<caso>.json` na mesma pasta. Também dá para rodar sem o Grasshopper:
`_-RunPythonScript "...\rhino\grasshopper\karamba_validar.py"` (o log sai na
linha de comando). Depois:

```powershell
.venv\Scripts\python.exe -m pytest testes\test_karamba.py -v -s
```

Se algo falhar, copie o painel de log (tem o erro completo).

## Pareto (Etapa 3)

1. Otimize (no PowerShell, na raiz do projeto):

   ```powershell
   .venv\Scripts\python.exe scripts\otimizar_exemplo.py trelica
   ```

   (ou `sala`, que precisa de um EPW em `data/epw`). Grava
   `resultados/<caso>/resultado.json` e `resultados/<caso>/paralelas.png`
   (coordenadas paralelas; linhas grossas = representante de cada cluster).

2. Uma vez, no Rhino 8:

   ```
   _-RunPythonScript "C:\Users\PC\Documents\plugin-otimizacao\rhino\grasshopper\montar_pareto.py"
   ```

   Cria `pareto.gh` com um componente GhPython ("Pareto", código de
   `pareto_rhino.py`, lido do disco), painel `caso`, botão `rodar` (roda o passo 1
   pelo Python da `.venv`; o GH fica travado até acabar), slider `solucao`, toggle
   `so_representantes` e painéis de log, parâmetros, objetivos e tabela.

3. Saídas: `geometria` (solução escolhida: malha opaca + vidros, ou barras),
   `vidros`, `todas` (a frente inteira lado a lado em x), `valores` (parâmetros
   da solução, para ligar em sliders da sua definição) e `tabela`. As soluções vêm
   em ordem do 1º objetivo: o slider percorre a frente de uma ponta à outra.

## Otimizar com a geometria do Grasshopper (Etapa 3b)

O Grasshopper **gera** a geometria de cada variante; o Python **avalia** a geração
inteira em lote na GPU e roda o otimizador (NSGA-II / NSGA-III). Os dois conversam por
HTTP em `localhost`, com o protocolo descrito abaixo (o plugin C# da Etapa 4 troca só
o lado do GH).

1. Uma vez, no Rhino 8:

   ```
   _-RunPythonScript "C:\Users\PC\Documents\plugin-otimizacao\rhino\grasshopper\montar_otimizar.py"
   ```

   Cria `otimizar.gh`: a "Sala exemplo" (sala do protótipo em Breps, `sala_exemplo.py`),
   5 sliders, o componente **Otimizar** (`otimizar_gh.py`, lido do disco), painéis
   `problema` e `opcoes`, botões `iniciar` / `parar` / `avaliar` e, embaixo, o
   **Pareto** (o mesmo do `pareto.gh`, com `caso = sala_gh`). Tudo numa definição só:
   o Pareto recarrega sozinho no fim da otimização e o `aplicar` acha os sliders.
   Se mudar o `caso` em `opcoes`, mude também o painel `caso` do Pareto.

2. Na sua definição: copie o Otimizar com os painéis e ligue
   - em `genoma`, os **sliders** (contínuo com as casas decimais do slider; inteiro se
     o slider for Integer) e **Value Lists** (categórico, pelos nomes dos itens) que o
     otimizador pode mexer. O nome do parâmetro é o NickName do slider;
   - a geometria nas entradas por rótulo: `fachada`, `vidro`, `cobertura`, `piso`,
     `interno` (projeto) e `contexto`, `entorno` (fixos: mandados uma vez só, não podem
     depender do genoma). Brep, Mesh, Surface ou Extrusion, em qualquer unidade (a
     escala vai para o servidor). Normais para fora. Malhas grandes em coordenadas
     enormes (UTM) perdem precisão em float32: modele perto da origem;
   - ou, para estrutura: `barras` (linhas/polilinhas), `apoios` e `cargas` (pontos),
     `secao` (nome do catálogo `TUBOS`, uma ou uma por barra).

3. `problema` (um objetivo ou restrição por linha):

   ```
   max sol_luz.da        objetivo com sentido escolhido
   sol_luz.ase           objetivo no sentido padrão da métrica
   sol_luz.udi >= 50     restrição
   ```

   A saída `metricas` lista tudo o que existe (unidade, sentido padrão).

4. `opcoes` (`chave = valor`, todas opcionais):

   | chave | padrão | |
   |---|---|---|
   | `caso` | gh | pasta `resultados/<caso>/` |
   | `populacao`, `geracoes`, `semente` | 48, 20, 0 | |
   | `algoritmo` | auto (1 obj. → ga, 2 → nsga2, 3+ → nsga3) | ga / cmaes (1 obj., só contínuos) / nsga2 / nsga3 / rvea |
   | `nivel` | rapido | rapido / padrao / validacao |
   | `epw` | 1º de `data/epw` | caminho do clima |
   | `malha` | minima | malha dos Breps: minima / rapida / padrao / qualidade (curvos: padrao) |
   | `lote` | 4 | tarefas pedidas por vez (pequeno divide melhor entre vários Rhinos) |
   | `modo` | principal | `trabalhador`: só ajuda outro Rhino (ver abaixo) |
   | `mostrar` | nao | `sim` redesenha a vista a cada variante (mais lento) |
   | `porta` | 8765 | |
   | `rho_<rótulo>`, `tau_vidro` | os do HB | materiais |
   | `apoio`, `forca`, `trelica`, `gravidade` | articulado, 0,0,-10, nao, 9.81 | estrutura (kN) |

5. `avaliar` avalia só a variante atual (confere se a geometria chega certa) e grava
   `testes/referencias/gh/variante_<caso>.json`. `iniciar` otimiza: o componente sobe o
   servidor se preciso (janela de console; a 1ª vez leva ~20 s), e o laço roda num timer
   da interface, em blocos de ~0,25 s (o Rhino continua respondendo; a vista não é
   redesenhada). Progresso na mensagem do componente e na barra de status; **Esc** ou
   `parar` interrompem (o que já foi avaliado é gravado). No fim os sliders voltam aos
   valores originais, o `resultado.json` é gravado e o Pareto recarrega; escolha a
   solução no slider `solucao` e aperte `aplicar` para levá-la aos sliders.

**Validação** (sala de exemplo × `prototipos.predio`): em `otimizar.gh`, deixe
`caso = sala_gh`, aperte `avaliar` e rode

```powershell
.venv\Scripts\python.exe -m pytest testes\test_gh.py -v -s
```

Ele mostra as áreas por rótulo, as métricas do `sol_luz` (GH × protótipo) e o tempo do GH.

**Vários Rhinos ao mesmo tempo (modo trabalhador).** O recálculo do GH é o gargalo
(uma variante por vez em cada Rhino); com mais Rhinos, a mesma geração é dividida entre
eles. No mesmo PC:

1. Rhino A (principal): abra o `otimizar.gh` como sempre.
2. Rhino B, C… (abra o Rhino de novo): abra o **mesmo** `otimizar.gh`, troque no painel
   `opcoes` para `modo = trabalhador` (não precisa salvar) e aperte `iniciar`. A mensagem
   fica "trabalhador · esperando otimização".
3. No Rhino A, aperte `iniciar`. Os trabalhadores entram sozinhos, pegam tarefas da mesma
   fila (`lote` por vez) e, quando a otimização acaba, voltam a esperar a próxima. O
   resultado e o Pareto ficam no Rhino A.

- `parar` num trabalhador: entrega o que já gerou e devolve o resto à fila (a otimização
  segue nos outros). `parar` (ou Esc) no principal encerra a otimização para todos.
  Atenção: o Esc é lido do teclado e vale para todos os Rhinos abertos; para parar um só,
  use o botão.
- O trabalhador confere se tem no `genoma` os mesmos nomes de parâmetros do principal;
  contexto, escala e materiais vêm do principal.
- Rhino que trava ou fecha no meio: as tarefas dele voltam à fila depois de 120 s.
- Quantos vale a pena: cada Rhino usa um núcleo da CPU e ~1–2 GB de RAM; o ganho é até
  ~N× enquanto a GPU (1,3 ms/variante na sala) não for o limite.

### Protocolo (versão 1)

Servidor: `.venv\Scripts\python.exe -m otimiza.servidor [--porta 8765]` (com
`PYTHONPATH=src`; o componente faz isso sozinho). HTTP/1.1 em `127.0.0.1`, corpo e
resposta em JSON UTF-8. Erro: HTTP 400 (pedido errado) ou 500, com `{"erro": "..."}`.
Todo pedido pode levar `"versao": 1`. NaN sai como `null`.

**Arrays.** Vértices: `x y z` seguidos; faces: `i j k` seguidos (triângulos, índice a
partir de 0). Cada array é uma lista JSON ou um texto **base64** de float32 (`f4`,
vértices) / int32 (`i4`, faces) little-endian. Pela RhinoCommon:
`Mesh.Vertices.ToFloatArray()` e `Mesh.Faces.ToIntArray(true)` + `Buffer.BlockCopy` +
`Convert.ToBase64String`. `"tipo_vertices": "f8"` aceita float64.

**Malhas por rótulo:** `{"fachada": malha | [malhas], "vidro": ..., ...}` com
`malha = {"vertices", "faces"}`. Rótulos: fachada, vidro, cobertura, piso, interno,
contexto, entorno.

**Estrutura:** `{"barras": [x1 y1 z1 x2 y2 z2 ...], "secao": "RO114.3/4" | {"D","t"} |
{"b","h"} | [uma por barra], "trelica": bool, "gravidade": 9.81, "material":
{"E","G","rho","fy"}?, "apoios": [{"pontos": [...], "condicoes": "articulado" |
"engaste" | "rolete_z" | [6 bools]}], "cargas": [{"pontos": [...], "forca": [3],
"momento": [3]?}]}`. Nós = pontas das barras unidas a 0,1 mm; kN, kN·m; seção em m.

**Geometria de uma variante:** `{"malhas": {...}}` ou `{"estrutura": {...}}`.

| rota | corpo | resposta |
|---|---|---|
| `GET /estado` | | `estado` (livre, gerando, avaliando, fim, erro), `rodada`, `parametros` (nomes), `geracao`, `geracoes`, `lote`, `faltam`, `avaliacoes`, `no_cache`, `erro`, `arquivo`, tempos |
| `GET /metricas` | | `{"metricas": {modulo: {metrica: {unidade, sentido, descricao}}}}` |
| `GET /progresso` | `?desde=g` (opcional) | o mesmo do `/estado` + `parametros_espaco`, `objetivos` [{nome, sentido}], `geracoes_registradas`, `historico` (por geração a partir de `desde`: `avaliacoes`, `viaveis`, `n_frente`, `melhor` por objetivo, `hipervolume` com normalização fixa da 1ª geração; 1 objetivo: null), `frente` (até 300: `indice`, `geracao`, `cluster`, `objetivos` em lista, `parametros`), `n_frente`. Usado pelos gráficos da janela de Otimização (4f2); só acrescentado, versão continua 1 |
| `POST /iniciar` | `parametros`, `problema`, `nivel`, `algoritmo`, `populacao`, `geracoes`, `semente`, `epw`, `escala`, `materiais`, `fixas`, `caso`, `saida`, `cliente`, `reatribuir_s` | estado (substitui a otimização anterior) |
| `POST /tarefas` | `{"maximo": 16}` (0 = todas) | estado + `tarefas: [{"id", "parametros": {nome: valor}}]`; lista vazia = aguardar (GPU avaliando ou outro cliente terminando) |
| `POST /resultados` | `{"resultados": [{"id", "geometria", "tempo_s"?} \| {"id", "erro"}]}` | estado + `aceitos`, `erros` |
| `POST /parar` | | estado (grava o que já foi avaliado) |
| `POST /devolver` | `{"ids": [...]}` | estado + `devolvidas`: tarefas pegas e não feitas voltam já à fila |
| `POST /avaliar` | `geometrias: [...]`, `metricas` ou `problema`, `nivel`, `epw`, `escala`, `materiais`, `fixas` | `{"valores": {"modulo.metrica": [...]}, "tempo_s"}` |
| `POST /encerrar` | | desliga o servidor |

- `parametros`: `[{"nome", "tipo": "continuo", "minimo", "maximo", "casas"?} |
  {"nome", "tipo": "inteiro", "minimo", "maximo"} | {"nome", "tipo": "categorico",
  "opcoes": [...]}]`. Com `casas`, o valor já vem arredondado como no slider.
- `problema`: lista de linhas (formato do item 3) ou um texto só.
- `escala`: unidade do modelo → metro (mm = 0,001). `materiais`: `{"fachada":
  {"rho": 0.5}, "vidro": {"tau": 0.64, "fs": 0.42}}` (`fs` = fator solar, usado no
  `termico.ganho_solar`). `fixas`: `{"malhas": {"contexto": ...}}`,
  juntadas a toda variante. `cliente`: qualquer JSON, gravado em
  `resultado.json → info.cliente` (o GH guarda o GUID de cada slider para o `aplicar`).
- Ciclo do cliente: `POST /iniciar` → repetir { `POST /tarefas` → gerar → `POST
  /resultados` } até `estado` = `fim` ou `erro`. O `id` traz a rodada (nº da otimização
  no servidor) e a geração (`"2-3-17"`): resposta atrasada de outra rodada ou geração é
  ignorada. Vários clientes podem seguir esse ciclo ao mesmo tempo (trabalhadores: sem
  o `/iniciar`; esperam `estado` = gerando no `GET /estado`). Projetos já avaliados (mesmos valores)
  não voltam como tarefa: o servidor reaproveita o resultado. Variante com `erro` ou
  geometria inválida vira inviável (não para a otimização).
- Saída: `resultados/<caso>/resultado.json` (formato da Etapa 3, lido pelo Pareto) e
  `paralelas.png`, gravados no fim, no `parar` e em caso de erro.

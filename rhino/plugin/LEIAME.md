# Plugin C# do Grasshopper (Etapa 4c)

`Otimiza.gha` traz o componente **Otimizar** (aba *Otimiza → Otimização*): o mesmo do
`rhino/grasshopper/otimizar_gh.py`, com as mesmas entradas, saídas e opções (ver
`rhino/grasshopper/LEIAME.md`, Etapa 3b), falando o mesmo protocolo v1 com o servidor
Python. O `otimizar_gh.py` continua funcionando.

| pasta | |
|---|---|
| `Otimiza.Cliente/` | protocolo v1 sem Rhino: HTTP (`Cliente.cs`), base64 (`Codificacao.cs`), painel de opções (`Opcoes.cs`) e o laço (`Ciclo.cs`: conectar, tarefas, resultados, trabalhador, `/devolver`, parar) |
| `Otimiza.UI/` | janela de Otimização (Eto, sem Grasshopper): objetivos, restrições, configuração e gráficos (4f2) |
| `web/` | página dos gráficos (`janela.html`, `graficos.js`) e o plotly.js 2.35.2 local (MIT, 4,56 MB, SHA-256 `bSEmbOG9fZ5atOEVmJxwwg3gOC/Zc6jyarWGGeuk1gM=`, do pacote npm plotly.js-dist-min); copiada para `bin/web/` na compilação |
| `Otimiza.GH/` | o `.gha`: componente, genoma (sliders/Value Lists), geometria → protocolo, timer da interface. Compila junto os arquivos do `Otimiza.Cliente` (um arquivo só) |
| `Otimiza.Testes/` | console que faz o papel do Rhino contra o servidor real (rodado pelo `testes/test_cliente_cs.py`) |
| `bin/` | saída da compilação (fora do git) |

## Compilar (precisa do .NET 8 SDK)

Feche o Rhino antes (o Grasshopper trava o `.gha` aberto). Na raiz do projeto:

```powershell
dotnet build rhino\plugin\Otimiza.GH -c Release
```

Gera `rhino\plugin\bin\Otimiza.gha`. Teste sem Rhino (compila e roda o console contra o
servidor Python):

```powershell
.venv\Scripts\python.exe -m pytest testes\test_cliente_cs.py -v -s
```

## Carregar no Rhino (uma vez)

1. No Rhino 8, digite `GrasshopperDeveloperSettings`, clique em **Add Folder** e escolha
   `C:\Users\PC\Documents\plugin-otimizacao\rhino\plugin\bin`. OK.
2. Reinicie o Rhino e abra o Grasshopper: aparece a aba **Otimiza**.
3. Monte a definição de exemplo (sala + sliders + Otimizar C# + Pareto):

   ```
   _-RunPythonScript "C:\Users\PC\Documents\plugin-otimizacao\rhino\grasshopper\montar_otimizar_cs.py"
   ```

   Cria `rhino/grasshopper/otimizar_cs.gh`. Daí em diante é igual ao `otimizar.gh`.

O componente acha a pasta do projeto subindo a partir do `.gha` (ou do `.gh`) até o
`CLAUDE.md`; se mover o plugin para outro lugar, use `raiz = <pasta>` no painel `opcoes`.

## Janela de Otimização (4f2)

**Duplo clique** no componente Otimizar (ou botão direito → *Abrir janela de Otimização…*).

- À esquerda: a lista de métricas do servidor (`GET /metricas`), com unidade e sentido.
  Marque 1 ou mais como objetivo e escolha min/max; acrescente restrições; nível,
  população, gerações e algoritmo (`auto`: 1 objetivo → ga, 2 → nsga2, 3+ → nsga3).
  Tudo fica salvo **dentro do componente** (no `.gh`).
- Quem manda: se um Panel estiver ligado em `problema`, ele manda no problema; se o
  painel `opcoes` tiver `nivel`, `populacao`, `geracoes` ou `algoritmo`, ele sobrescreve a
  janela. A janela avisa nos dois casos.
- **Iniciar** / **Parar** fazem o mesmo que os botões do componente.
- À direita (WebView2 + plotly.js, sem internet): convergência (hipervolume por geração;
  com 1 objetivo, o melhor valor), frente de Pareto 2D/3D com clusters, coordenadas
  paralelas (objetivos + parâmetros) e a tabela das soluções. Atualiza a cada 0,5 s
  (`GET /progresso`). Sem otimização no servidor, mostra o último
  `resultados/<caso>/resultado.json`.
- Clique numa solução (ponto ou linha da tabela) e em **Aplicar no Grasshopper**: os
  sliders recebem os valores e a geometria aparece no Rhino (só com a otimização parada).

Conferência: `rhino/grasshopper/conferir_janela.py` (roteiro no próprio arquivo).

# Plugin C# do Grasshopper (Etapa 4c)

`Otimiza.gha` traz o componente **Otimizar** (aba *Otimiza → Otimização*): o mesmo do
`rhino/grasshopper/otimizar_gh.py`, com as mesmas entradas, saídas e opções (ver
`rhino/grasshopper/LEIAME.md`, Etapa 3b), falando o mesmo protocolo v1 com o servidor
Python. O `otimizar_gh.py` continua funcionando.

| pasta | |
|---|---|
| `Otimiza.Cliente/` | protocolo v1 sem Rhino: HTTP (`Cliente.cs`), base64 (`Codificacao.cs`), painel de opções (`Opcoes.cs`) e o laço (`Ciclo.cs`: conectar, tarefas, resultados, trabalhador, `/devolver`, parar) |
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

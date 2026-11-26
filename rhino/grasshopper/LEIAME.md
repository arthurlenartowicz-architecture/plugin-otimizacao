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

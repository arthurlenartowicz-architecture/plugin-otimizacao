// Cliente C# contra o servidor Python real, sem Rhino. Uso:
//   Otimiza.Testes <url> <relatorio.json> <pasta de saída> <epw>
// Gera caixas paramétricas (em mm, base64 float32/int32, por rótulo), contexto em `fixas`,
// e roda: /metricas, /avaliar, e uma otimização com 1 principal + 2 trabalhadores
// (um para no meio e devolve tarefas; o outro espera a próxima no fim).
// O testes/test_cliente_cs.py confere tudo contra a conta analítica.
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text.Json.Nodes;
using System.Threading;
using Otimiza.Cliente;

static class Programa
{
    const double Mm = 1000.0;   // modelo em mm (escala 0,001)

    // Retângulo a-b-c-d (anti-horário visto de fora) → 2 triângulos.
    static void Quad(List<float> v, List<int> f, params double[][] p)
    {
        int i0 = v.Count / 3;
        foreach (var q in p) { v.Add((float)(q[0] * Mm)); v.Add((float)(q[1] * Mm)); v.Add((float)(q[2] * Mm)); }
        f.AddRange(new[] { i0, i0 + 1, i0 + 2, i0, i0 + 2, i0 + 3 });
    }

    static double[] P(double x, double y, double z) => new[] { x, y, z };

    /// <summary>Caixa W × D × H (m) sem base; com vidro_sul a fachada sul inteira é vidro.</summary>
    static JsonObject Caixa(double W, double D, double H, bool vidroSul, double x0 = 0)
    {
        var rot = new Dictionary<string, (List<float> v, List<int> f)>();
        (List<float>, List<int>) R(string r)
        {
            if (!rot.ContainsKey(r)) rot[r] = (new List<float>(), new List<int>());
            return rot[r];
        }
        double a = x0, b = x0 + W;
        var (vs, fs) = R(vidroSul ? "vidro" : "fachada");
        Quad(vs, fs, P(a, 0, 0), P(b, 0, 0), P(b, 0, H), P(a, 0, H));          // S (−y)
        var (vf, ff) = R("fachada");
        Quad(vf, ff, P(b, 0, 0), P(b, D, 0), P(b, D, H), P(b, 0, H));          // L (+x)
        Quad(vf, ff, P(b, D, 0), P(a, D, 0), P(a, D, H), P(b, D, H));          // N (+y)
        Quad(vf, ff, P(a, D, 0), P(a, 0, 0), P(a, 0, H), P(a, D, H));          // O (−x)
        var (vc, fc) = R("cobertura");
        Quad(vc, fc, P(a, 0, H), P(b, 0, H), P(b, D, H), P(a, D, H));          // topo (+z)
        var malhas = new JsonObject();
        foreach (var kv in rot) malhas[kv.Key] = Codificacao.Malha(kv.Value.v.ToArray(), kv.Value.f.ToArray());
        return malhas;
    }

    static JsonObject Geometria(JsonObject p) =>
        new JsonObject
        {
            ["malhas"] = Caixa(J.Num(p["largura"]), J.Num(p["profundidade"]), J.Num(p["altura"]),
                               J.Texto(p["vidro_sul"]) == "sim")
        };

    static JsonArray Lista(params string[] s) => new JsonArray(s.Select(x => (JsonNode)JsonValue.Create(x)!).ToArray());

    static int Main(string[] args)
    {
        string url = args[0], relatorio = args[1], saida = args[2], epw = args[3];
        var rel = new JsonObject();
        using var cli = new ClienteProtocolo(url);

        // codificação: o pytest decodifica e compara
        rel["b64_f4"] = Codificacao.Base64(new[] { 0f, 1.5f, -2.25f, 1e6f, 3.14159f });
        rel["b64_i4"] = Codificacao.Base64(new[] { 0, 1, 2, -1, int.MaxValue });

        var met = cli.Metricas()["metricas"] as JsonObject;
        rel["tem_compacidade"] = met?["termico"]?["compacidade"] != null;

        var fixas = new JsonObject { ["malhas"] = new JsonObject { ["contexto"] = Caixa(10, 10, 30, false, 60)["fachada"]!.DeepClone() } };
        var chaves = Lista("termico.compacidade", "termico.volume", "termico.area_envoltoria", "termico.wwr", "termico.wwr_S");

        // /avaliar: duas caixas
        var av = cli.Avaliar(new JsonObject
        {
            ["versao"] = 1, ["nivel"] = "rapido", ["escala"] = 1.0 / Mm, ["epw"] = epw,
            ["metricas"] = chaves.DeepClone(), ["fixas"] = fixas.DeepClone(),
            ["geometrias"] = new JsonArray(new JsonObject { ["malhas"] = Caixa(6, 8, 3, false) },
                                           new JsonObject { ["malhas"] = Caixa(10, 5, 4.5, true) }),
        });
        rel["avaliar"] = av["valores"]!.DeepClone();

        // programa (4e): o que os componentes Lote/Programa/Ambiente montam, e o /avaliar com isso
        var lote = ProgramaJson.Lote(new List<double[]> { P(-5, -10, 0), P(15, -10, 0), P(15, 20, 0), P(-5, 20, 0), P(-5, -10, 0) });
        var prog = ProgramaJson.Montar(ProgramaJson.Juntar(new[] { lote.ToJsonString() }), new[] { "sala", "quarto" },
                                       new[] { 20.0, 12.0 }, new[] { "sala-quarto:2", "sala, cozinha" });
        var juntos = ProgramaJson.Juntar(new[] { prog.ToJsonString(), "{\"adjacencia\": {\"parede_max\": 0.3}}",
                                                 "{\"adjacencia\": {\"comprimento_min\": 1}}" }, "C:/x/p.json");
        int errosProg = 0;
        foreach (var ruim in new Action[]
                 {
                     () => ProgramaJson.Lote(new List<double[]> { P(0, 0, 0), P(1, 1, 0), P(2, 2, 0) }),
                     () => ProgramaJson.Montar(null, new[] { "sala" }, new double[0], new string[0]),
                     () => ProgramaJson.Montar(null, new[] { "sala", "sala" }, new[] { 1.0, 2.0 }, new string[0]),
                     () => ProgramaJson.Montar(null, new[] { "sala" }, new[] { -1.0 }, new string[0]),
                     () => ProgramaJson.Adjacencia("sala"),
                     () => ProgramaJson.Adjacencia("sala-sala"),
                     () => ProgramaJson.Adjacencia("sala-quarto:x"),
                     () => ProgramaJson.Juntar(new[] { "sala = 20" }),
                 })
        {
            try { ruim(); } catch (ArgumentException) { errosProg++; }
        }
        var vp = new List<float>(); var fp = new List<int>();
        Quad(vp, fp, P(0, 0, 0), P(4, 0, 0), P(4, 5, 0), P(0, 5, 0));
        var vq = new List<float>(); var fq = new List<int>();
        Quad(vq, fq, P(0, 5.15, 0), P(4, 5.15, 0), P(4, 8, 0), P(0, 8, 0));
        var casa = Caixa(6, 8, 3, false);
        var sala = Codificacao.Malha(vp.ToArray(), fp.ToArray()); sala["ambiente"] = "sala";
        var quarto = Codificacao.Malha(vq.ToArray(), fq.ToArray()); quarto["ambiente"] = "quarto";
        casa["piso"] = new JsonArray(sala, quarto);
        var avp = cli.Avaliar(new JsonObject
        {
            ["versao"] = 1, ["nivel"] = "padrao", ["escala"] = 1.0 / Mm, ["programa"] = prog.DeepClone(),
            ["metricas"] = Lista("programa.ca", "programa.taxa_ocupacao", "programa.recuo_min", "programa.altura",
                                 "programa.atendimento_programa", "programa.adjacencia"),
            ["geometrias"] = new JsonArray(new JsonObject { ["malhas"] = casa }),
        });
        rel["programa"] = new JsonObject
        {
            ["lote"] = lote, ["prog"] = prog, ["juntos"] = juntos, ["resumo"] = ProgramaJson.Resumo(juntos),
            ["erros"] = errosProg, ["necessidades"] = Opcoes.De(new[] { "necessidades = C:/x/p.json" }).Necessidades(),
            ["sem_necessidades"] = Opcoes.De(new[] { "nivel = rapido" }).Necessidades() == null,
            ["valores"] = avp["valores"]!.DeepClone(),
        };

        // otimização: 1 principal + 2 trabalhadores, cada um com o seu cliente e a sua thread
        var parametros = new JsonArray(
            new JsonObject { ["nome"] = "largura", ["tipo"] = "continuo", ["minimo"] = 4.0, ["maximo"] = 12.0, ["casas"] = 2 },
            new JsonObject { ["nome"] = "profundidade", ["tipo"] = "inteiro", ["minimo"] = 4, ["maximo"] = 10 },
            new JsonObject { ["nome"] = "altura", ["tipo"] = "continuo", ["minimo"] = 2.5, ["maximo"] = 6.0, ["casas"] = 1 },
            new JsonObject { ["nome"] = "vidro_sul", ["tipo"] = "categorico", ["opcoes"] = Lista("nao", "sim") });
        var nomes = new[] { "largura", "profundidade", "altura", "vidro_sul" };

        // modelo da janela de Otimização (4f2): o problema da otimização sai dele
        var catalogo = Catalogo.De(cli.Metricas());
        var cfg = new Configuracao { Populacao = 16, Geracoes = 4 };
        cfg.Objetivos.Add(new Objetivo { Chave = "termico.compacidade", Sentido = "min" });
        cfg.Objetivos.Add(new Objetivo { Chave = "termico.volume", Sentido = "max" });
        cfg.Restricoes.Add(new Restricao { Chave = "termico.wwr", Operador = "<=", Valor = 30 });
        var vazia = new Configuracao();
        var gaDois = Configuracao.DeJson(cfg.ParaJson());
        gaDois.Algoritmo = "ga";
        var deTexto = Configuracao.DeProblema(new[] { "sol_luz.da\nmin sol_luz.ase # comentário", "sol_luz.udi >= 50" }, catalogo);
        var opMisto = Opcoes.De(cfg.ComoOpcoes().Concat(new[] { "populacao = 99" }));
        rel["janela"] = new JsonObject
        {
            ["catalogo"] = new JsonArray(catalogo.Select(m => (JsonNode)new JsonObject
                { ["chave"] = m.Chave, ["unidade"] = m.Unidade, ["sentido"] = m.Sentido }).ToArray()),
            ["problema"] = Lista(cfg.Problema().ToArray()),
            ["ida_volta"] = Configuracao.DeJson(JsonNode.Parse(cfg.ParaJson().ToJsonString())).ParaJson().ToJsonString()
                            == cfg.ParaJson().ToJsonString(),
            ["de_texto"] = deTexto.ParaJson(),
            ["erros_vazia"] = Lista(vazia.Validar().ToArray()),
            ["erros_ga_dois"] = Lista(gaDois.Validar().ToArray()),
            ["auto"] = Lista(Configuracao.AlgoritmoAuto(1), Configuracao.AlgoritmoAuto(2), Configuracao.AlgoritmoAuto(3)),
            ["opcoes_populacao"] = opMisto.Int("populacao", 0), ["opcoes_nivel"] = opMisto.Texto("nivel", ""),
            // painel "fatores = ..." → campo carbono_custo do pedido (4d)
            ["fatores"] = Opcoes.De(new[] { "fatores = C:/x/meus.json" }).CarbonoCusto()?.ToJsonString(),
            ["sem_fatores"] = Opcoes.De(new[] { "nivel = rapido" }).CarbonoCusto() == null,
        };
        var genoma = new JsonArray(nomes.Select(n => (JsonNode)new JsonObject
        { ["nome"] = n, ["guid"] = "guid-" + n, ["tipo"] = "x" }).ToArray());
        var pedido = new JsonObject
        {
            ["versao"] = 1, ["parametros"] = parametros,
            ["problema"] = Lista(cfg.Problema().ToArray()),     // o mesmo texto da janela
            ["nivel"] = "rapido", ["algoritmo"] = "auto", ["populacao"] = 16, ["geracoes"] = 4, ["semente"] = 3,
            ["escala"] = 1.0 / Mm, ["materiais"] = new JsonObject(), ["epw"] = epw, ["caso"] = "teste_cs",
            ["saida"] = saida, ["fixas"] = fixas,
            ["cliente"] = new JsonObject { ["genoma"] = genoma, ["arquivo_gh"] = "teste" },
        };
        Gerado Gerar(Tarefa t)
        {
            if (J.Num(t.Parametros["largura"]) > 11.5)        // erro de propósito → inviável
                return new Gerado(null, "teste: largura > 11,5");
            return new Gerado(Geometria(t.Parametros));
        }
        Gerado GerarDevagar(Tarefa t) { Thread.Sleep(3); return Gerar(t); }
        // principal lento (50 ms, como um GH pesado): os trabalhadores consultam o servidor
        // a cada 1 s e precisam de tempo para entrar
        Gerado GerarPrincipal(Tarefa t) { Thread.Sleep(50); return Gerar(t); }
        bool acabou = false;

        // rodada 1 (antes da principal): trabalhador com genoma diferente recusa entrar
        var nova = (JsonObject)pedido.DeepClone();
        nova["geracoes"] = 1;
        nova["saida"] = saida + "_w3";
        cli.Iniciar(nova);
        using (var cliW3 = new ClienteProtocolo(url))
        {
            var w3 = new Ciclo(cliW3, null, new[] { "largura" }, Gerar);
            for (int k = 0; k < 50 && w3.Ativo; k++) { w3.Passo(0.01); Thread.Sleep(20); }
            rel["w3"] = new JsonObject { ["estado"] = w3.Estado, ["log"] = Lista(w3.Log.ToArray()) };
        }
        cli.Parar();

        using var cliW1 = new ClienteProtocolo(url);
        using var cliW2 = new ClienteProtocolo(url);
        var w1 = new Ciclo(cliW1, null, nomes, GerarDevagar) { Lote = 4 };
        var w2 = new Ciclo(cliW2, null, nomes, GerarDevagar) { Lote = 3 };
        bool w2Esperou = false;
        w2.AoEsperar += () => w2Esperou = true;
        string? terminou = null;
        Ciclo? principal = null;
        var prazo = DateTime.Now.AddSeconds(240);

        var tW1 = new Thread(() =>
        {
            while (w1.Ativo && !Volatile.Read(ref acabou) && DateTime.Now < prazo)
            {
                w1.Passo(0.004);
                if (w1.Gerados >= 6 && w1.Pendentes > 0) w1.Parar("teste");   // devolve o resto
                Thread.Sleep(1);
            }
        });
        var tW2 = new Thread(() =>
        {
            var limite = DateTime.MaxValue;   // depois do fim do principal, 5 s para notar o fim
            while (w2.Ativo && !w2Esperou && DateTime.Now < prazo && DateTime.Now < limite)
            {
                w2.Passo(0.004);
                if (limite == DateTime.MaxValue && Volatile.Read(ref acabou)) limite = DateTime.Now.AddSeconds(5);
                Thread.Sleep(1);
            }
            w2.Parar("teste");
        });
        tW1.Start();
        tW2.Start();
        Thread.Sleep(300);     // os trabalhadores já estão esperando quando o principal inicia
        principal = new Ciclo(cli, pedido, nomes, GerarPrincipal) { Lote = 4 };
        principal.AoTerminar += e => terminou = e;
        while (principal.Ativo && DateTime.Now < prazo) { principal.Passo(0.05); Thread.Sleep(1); }
        Volatile.Write(ref acabou, true);
        tW1.Join(30000);
        tW2.Join(30000);

        rel["principal"] = new JsonObject
        {
            ["estado"] = principal.Estado, ["terminou"] = terminou, ["gerados"] = principal.Gerados,
            ["rodada"] = principal.Rodada, ["ultimo"] = principal.Ultimo.DeepClone(),
            ["log"] = Lista(principal.Log.ToArray()),
        };
        rel["w1"] = new JsonObject { ["estado"] = w1.Estado, ["gerados"] = w1.Gerados, ["devolvidas"] = w1.Devolvidas,
                                     ["log"] = Lista(w1.Log.ToArray()) };
        rel["w2"] = new JsonObject { ["estado"] = w2.Estado, ["gerados"] = w2.Gerados, ["esperou"] = w2Esperou,
                                     ["log"] = Lista(w2.Log.ToArray()) };

        // gráficos: GET /progresso no fim e o resultado.json convertido para o mesmo formato
        var pg = cli.Pedir("GET", "/progresso");
        rel["progresso"] = pg.DeepClone();
        rel["arquivo"] = DadosGraficos.DeResultadoJson(Path.Combine(saida, "resultado.json"));

        File.WriteAllText(relatorio, rel.ToJsonString(ClienteProtocolo.Json));
        return 0;
    }
}

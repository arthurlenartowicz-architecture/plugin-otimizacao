// Modelo da janela de Otimização (4f2), sem tela: catálogo de métricas do servidor, a
// configuração escolhida (salva dentro do componente, no .gh) e o problema em texto do
// protocolo v1. Fica no Otimiza.Cliente para o console de testes usar o mesmo código.
using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text.Json.Nodes;

namespace Otimiza.Cliente
{
    public sealed class Metrica
    {
        public string Chave = "";        // "modulo.metrica"
        public string Unidade = "";
        public string Sentido = "min";   // sentido padrão do módulo
        public string Descricao = "";
    }

    public static class Catalogo
    {
        /// <summary>Métricas de GET /metricas, em ordem de módulo e nome.</summary>
        public static List<Metrica> De(JsonObject resposta)
        {
            var l = new List<Metrica>();
            if (resposta["metricas"] is not JsonObject mods) return l;
            foreach (var mod in mods.OrderBy(kv => kv.Key, StringComparer.Ordinal))
                if (mod.Value is JsonObject ms)
                    foreach (var kv in ms.OrderBy(kv => kv.Key, StringComparer.Ordinal))
                        l.Add(new Metrica
                        {
                            Chave = mod.Key + "." + kv.Key,
                            Unidade = J.Texto(kv.Value?["unidade"]) ?? "",
                            Sentido = J.Texto(kv.Value?["sentido"]) == "max" ? "max" : "min",
                            Descricao = J.Texto(kv.Value?["descricao"]) ?? "",
                        });
            return l;
        }
    }

    public sealed class Objetivo
    {
        public string Chave = "";
        public string Sentido = "min";
    }

    public sealed class Restricao
    {
        public string Chave = "";
        public string Operador = "<=";
        public double Valor;
    }

    public sealed class Configuracao
    {
        public static readonly string[] Niveis = { "rapido", "padrao", "validacao" };
        public static readonly string[] Algoritmos = { "auto", "ga", "cmaes", "nsga2", "nsga3", "rvea" };

        public List<Objetivo> Objetivos = new List<Objetivo>();
        public List<Restricao> Restricoes = new List<Restricao>();
        public string Nivel = "rapido";
        public int Populacao = 48;
        public int Geracoes = 20;
        public string Algoritmo = "auto";

        /// <summary>Linhas do problema do protocolo v1 ("max x.y", "x.y &lt;= v").</summary>
        public List<string> Problema()
        {
            var erros = Validar();
            if (erros.Count > 0) throw new InvalidOperationException(string.Join("; ", erros));
            var l = Objetivos.Select(o => o.Sentido + " " + o.Chave).ToList();
            l.AddRange(Restricoes.Select(r => r.Chave + " " + r.Operador + " "
                                              + r.Valor.ToString("R", CultureInfo.InvariantCulture)));
            return l;
        }

        public List<string> Validar()
        {
            var e = new List<string>();
            if (Objetivos.Count == 0) e.Add("marque pelo menos um objetivo");
            foreach (var g in Objetivos.GroupBy(o => o.Chave).Where(g => g.Count() > 1))
                e.Add("objetivo repetido: " + g.Key);
            foreach (var o in Objetivos)
                if (o.Sentido != "min" && o.Sentido != "max") e.Add("sentido inválido em " + o.Chave);
            foreach (var r in Restricoes)
            {
                if (string.IsNullOrWhiteSpace(r.Chave)) e.Add("restrição sem métrica");
                if (r.Operador != "<=" && r.Operador != ">=") e.Add("operador inválido em " + r.Chave);
                if (double.IsNaN(r.Valor) || double.IsInfinity(r.Valor)) e.Add("valor inválido em " + r.Chave);
            }
            if (Populacao < 2) e.Add("população < 2");
            if (Geracoes < 1) e.Add("gerações < 1");
            if (!Niveis.Contains(Nivel)) e.Add("nível inválido: " + Nivel);
            if (!Algoritmos.Contains(Algoritmo)) e.Add("algoritmo inválido: " + Algoritmo);
            if ((Algoritmo == "ga" || Algoritmo == "cmaes") && Objetivos.Count > 1)
                e.Add(Algoritmo + " é para 1 objetivo");
            if ((Algoritmo == "nsga3" || Algoritmo == "rvea") && Objetivos.Count == 1)
                e.Add(Algoritmo + " precisa de 2 ou mais objetivos");
            return e;
        }

        /// <summary>O que o "auto" do servidor escolhe (otimizador.criar_algoritmo).</summary>
        public static string AlgoritmoAuto(int nObjetivos) =>
            nObjetivos <= 1 ? "ga" : nObjetivos == 2 ? "nsga2" : "nsga3";

        public string AlgoritmoEfetivo() => Algoritmo == "auto" ? AlgoritmoAuto(Objetivos.Count) : Algoritmo;

        /// <summary>Linhas "chave = valor" que entram antes do painel opcoes (o painel manda).</summary>
        public List<string> ComoOpcoes() => new List<string>
        {
            "nivel = " + Nivel,
            "populacao = " + Populacao.ToString(CultureInfo.InvariantCulture),
            "geracoes = " + Geracoes.ToString(CultureInfo.InvariantCulture),
            "algoritmo = " + Algoritmo,
        };

        public static readonly string[] ChavesOpcoes = { "nivel", "populacao", "geracoes", "algoritmo" };

        // ------------------------------------------------------------ JSON (salvo no .gh)
        public JsonObject ParaJson() => new JsonObject
        {
            ["versao"] = 1,
            ["objetivos"] = new JsonArray(Objetivos.Select(o => (JsonNode)new JsonObject
                { ["chave"] = o.Chave, ["sentido"] = o.Sentido }).ToArray()),
            ["restricoes"] = new JsonArray(Restricoes.Select(r => (JsonNode)new JsonObject
                { ["chave"] = r.Chave, ["operador"] = r.Operador, ["valor"] = r.Valor }).ToArray()),
            ["nivel"] = Nivel, ["populacao"] = Populacao, ["geracoes"] = Geracoes, ["algoritmo"] = Algoritmo,
        };

        public static Configuracao DeJson(JsonNode? n)
        {
            var c = new Configuracao();
            if (n is not JsonObject d) return c;
            if (d["objetivos"] is JsonArray os)
                foreach (var o in os.OfType<JsonObject>())
                    c.Objetivos.Add(new Objetivo { Chave = J.Texto(o["chave"]) ?? "", Sentido = J.Texto(o["sentido"]) ?? "min" });
            if (d["restricoes"] is JsonArray rs)
                foreach (var r in rs.OfType<JsonObject>())
                    c.Restricoes.Add(new Restricao
                    {
                        Chave = J.Texto(r["chave"]) ?? "", Operador = J.Texto(r["operador"]) ?? "<=",
                        Valor = J.Num(r["valor"], 0),
                    });
            c.Nivel = J.Texto(d["nivel"]) ?? c.Nivel;
            c.Populacao = J.Int(d["populacao"], c.Populacao);
            c.Geracoes = J.Int(d["geracoes"], c.Geracoes);
            c.Algoritmo = J.Texto(d["algoritmo"]) ?? c.Algoritmo;
            return c;
        }

        /// <summary>Lê linhas de problema em texto (o painel 'problema'), para começar a janela
        /// com o que já estava no painel. Sentido não informado = o padrão do catálogo.</summary>
        public static Configuracao DeProblema(IEnumerable<string> linhas, IList<Metrica> catalogo)
        {
            var c = new Configuracao();
            foreach (var item in linhas)
                foreach (var bruta in item.Replace("\r", "").Split('\n'))
                {
                    var t = bruta.Split('#')[0].Trim();
                    if (t == "") continue;
                    var p = t.Split((char[]?)null, StringSplitOptions.RemoveEmptyEntries);
                    if (p.Length == 3 && (p[1] == "<=" || p[1] == ">=")
                        && double.TryParse(p[2], NumberStyles.Float, CultureInfo.InvariantCulture, out var v))
                        c.Restricoes.Add(new Restricao { Chave = p[0], Operador = p[1], Valor = v });
                    else if (p.Length == 2 && (p[0] == "max" || p[0] == "min"))
                        c.Objetivos.Add(new Objetivo { Chave = p[1], Sentido = p[0] });
                    else if (p.Length == 1)
                        c.Objetivos.Add(new Objetivo
                        {
                            Chave = p[0], Sentido = catalogo.FirstOrDefault(m => m.Chave == p[0])?.Sentido ?? "min",
                        });
                }
            return c;
        }
    }

    /// <summary>Dados dos gráficos: GET /progresso, ou um resultado.json gravado (sem
    /// histórico), no mesmo formato.</summary>
    public static class DadosGraficos
    {
        public static JsonObject DeResultadoJson(string arquivo)
        {
            var d = JsonNode.Parse(File.ReadAllText(arquivo)) as JsonObject
                    ?? throw new InvalidDataException("resultado.json inválido");
            var objs = (d["objetivos"] as JsonArray ?? new JsonArray()).OfType<JsonObject>().ToList();
            var nomes = objs.Select(o => J.Texto(o["nome"]) ?? "").ToList();
            var frente = new JsonArray();
            foreach (var s in (d["solucoes"] as JsonArray ?? new JsonArray()).OfType<JsonObject>())
                frente.Add(new JsonObject
                {
                    ["indice"] = J.Int(s["indice"]), ["geracao"] = J.Int(s["geracao"]),
                    ["cluster"] = J.Int(s["cluster"]),
                    ["objetivos"] = new JsonArray(nomes.Select(n => (JsonNode)J.Num(s["objetivos"]?[n])).ToArray()),
                    ["parametros"] = s["parametros"]?.DeepClone(),
                });
            return new JsonObject
            {
                ["estado"] = "arquivo", ["arquivo"] = arquivo, ["algoritmo"] = d["algoritmo"]?.DeepClone(),
                ["objetivos"] = new JsonArray(objs.Select(o => o.DeepClone()).ToArray()),
                ["geracoes_registradas"] = 0, ["historico"] = new JsonArray(),
                ["frente"] = frente, ["n_frente"] = frente.Count,
                ["avaliacoes"] = d["n_avaliacoes"]?.DeepClone(),
                ["parametros_espaco"] = d["parametros"]?.DeepClone(),
            };
        }
    }
}

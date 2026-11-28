// Programa de necessidades e lote no formato do programa.json (módulo programa, 4e).
// Usado pelos componentes Lote e Programa e pelo Otimizar; sem Rhino (testado no Otimiza.Testes).
using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Text.Json.Nodes;

namespace Otimiza.Cliente
{
    public static class ProgramaJson
    {
        static readonly CultureInfo Inv = CultureInfo.InvariantCulture;

        /// <summary>Área (m², sem sinal) do polígono pelos pontos [x, y, ...].</summary>
        public static double Area(IReadOnlyList<double[]> pts)
        {
            double s = 0;
            for (int i = 0; i < pts.Count; i++)
            {
                var a = pts[i]; var b = pts[(i + 1) % pts.Count];
                s += a[0] * b[1] - b[0] * a[1];
            }
            return Math.Abs(s) / 2;
        }

        /// <summary>{"lote": [[x, y, z], ...]} com os pontos já em metros (sem o ponto repetido
        /// de curva fechada). Erro se tiver menos de 3 pontos ou não tiver área.</summary>
        public static JsonObject Lote(IReadOnlyList<double[]> pontos)
        {
            var p = pontos.ToList();
            if (p.Count > 3 && Math.Abs(p[0][0] - p[^1][0]) < 1e-9 && Math.Abs(p[0][1] - p[^1][1]) < 1e-9)
                p.RemoveAt(p.Count - 1);
            if (p.Count < 3) throw new ArgumentException("o lote precisa de pelo menos 3 pontos");
            if (Area(p) < 1e-9) throw new ArgumentException("o lote não tem área (pontos alinhados ou repetidos)");
            var arr = new JsonArray();
            foreach (var q in p)
                arr.Add(new JsonArray(q.Take(3).Select(x => (JsonNode)JsonValue.Create(x)).ToArray()));
            return new JsonObject { ["lote"] = arr };
        }

        /// <summary>"sala-cozinha", "sala-cozinha:2", "sala, cozinha, 2" ou "sala; cozinha" → (a, b, peso).</summary>
        public static (string a, string b, double peso) Adjacencia(string texto)
        {
            string t = texto.Trim(), par = t;
            double peso = 1;
            int dp = t.LastIndexOf(':');
            if (dp >= 0)
            {
                par = t.Substring(0, dp);
                if (!double.TryParse(t.Substring(dp + 1).Trim().Replace(',', '.'), NumberStyles.Float, Inv, out peso))
                    throw new ArgumentException("peso da adjacência não é número: " + texto);
            }
            var partes = (par.IndexOfAny(new[] { ',', ';' }) >= 0 ? par.Split(',', ';') : par.Split('-'))
                .Select(s => s.Trim()).ToList();
            if (partes.Count == 3 && dp < 0 && double.TryParse(partes[2].Replace(',', '.'), NumberStyles.Float, Inv, out var w))
            {
                peso = w; partes.RemoveAt(2);
            }
            if (partes.Count != 2 || partes.Any(s => s.Length == 0))
                throw new ArgumentException("adjacência deve ser 'a-b' ou 'a-b:peso' (nomes sem '-': use 'a, b'): " + texto);
            if (partes[0] == partes[1]) throw new ArgumentException("adjacência com o mesmo uso dos dois lados: " + texto);
            if (!(peso > 0)) throw new ArgumentException("peso da adjacência deve ser > 0: " + texto);
            return (partes[0], partes[1], peso);
        }

        /// <summary>Programa = base (ex.: o lote) + áreas-meta por uso + adjacências.</summary>
        public static JsonObject Montar(JsonObject? baseObj, IList<string> usos, IList<double> areas,
                                        IList<string> adjacencias)
        {
            var d = (baseObj?.DeepClone() as JsonObject) ?? new JsonObject();
            if (usos.Count != areas.Count)
                throw new ArgumentException($"usos ({usos.Count}) e areas ({areas.Count}) precisam ter o mesmo tamanho");
            if (usos.Count > 0)
            {
                var a = new JsonObject();
                for (int i = 0; i < usos.Count; i++)
                {
                    var nome = (usos[i] ?? "").Trim();
                    if (nome.Length == 0) throw new ArgumentException($"uso {i + 1} sem nome");
                    if (a.ContainsKey(nome)) throw new ArgumentException("uso repetido: " + nome + " (some as áreas num só)");
                    if (!(areas[i] > 0)) throw new ArgumentException($"área-meta de {nome} deve ser > 0");
                    a[nome] = areas[i];
                }
                d["areas"] = a;
            }
            var adj = new JsonArray();
            foreach (var t in adjacencias.Where(s => !string.IsNullOrWhiteSpace(s)))
            {
                var (x, y, w) = Adjacencia(t);
                adj.Add(new JsonArray(x, y, w));
            }
            if (adj.Count > 0) d["adjacencias"] = adj;
            return d;
        }

        /// <summary>Junta os textos JSON ligados na entrada programa (Lote, Programa…), em ordem;
        /// `arquivo` (painel necessidades = …) vem antes. "adjacencia" é combinada; o resto, trocado.</summary>
        public static JsonObject Juntar(IEnumerable<string> textos, string? arquivo = null)
        {
            var d = new JsonObject();
            if (!string.IsNullOrWhiteSpace(arquivo)) d["arquivo"] = arquivo;
            foreach (var t in textos.Where(s => !string.IsNullOrWhiteSpace(s)))
            {
                JsonObject x;
                try { x = JsonNode.Parse(t) as JsonObject ?? throw new ArgumentException("não é um objeto"); }
                catch (Exception ex) when (!(ex is ArgumentException))
                {
                    throw new ArgumentException("programa: texto não é JSON (ligue a saída dos componentes Lote/Programa): "
                                                + ex.Message);
                }
                foreach (var kv in x.ToList())
                {
                    if (kv.Key == "adjacencia" && d["adjacencia"] is JsonObject velho && kv.Value is JsonObject novo)
                        foreach (var p in novo) velho[p.Key] = p.Value?.DeepClone();
                    else
                        d[kv.Key] = kv.Value?.DeepClone();
                }
            }
            return d;
        }

        /// <summary>Uma linha para o usuário conferir o que vai ao servidor.</summary>
        public static string Resumo(JsonObject p)
        {
            var partes = new List<string>();
            if (p["lote"] is JsonArray l)
            {
                var pts = l.OfType<JsonArray>().Select(q => q.Select(x => x!.GetValue<double>()).ToArray()).ToList();
                partes.Add(string.Format(Inv, "lote {0:0.##} m² ({1} pontos)", Area(pts), pts.Count));
            }
            if (p["areas"] is JsonObject a)
                partes.Add(string.Format(Inv, "{0} usos, {1:0.##} m² de meta", a.Count, a.Sum(kv => kv.Value!.GetValue<double>())));
            if (p["adjacencias"] is JsonArray j) partes.Add(j.Count + " adjacências");
            if (p["arquivo"] != null) partes.Add("arquivo " + p["arquivo"]);
            return partes.Count > 0 ? string.Join(" · ", partes) : "programa vazio";
        }
    }
}

// Painel "chave = valor" do componente (mesmas regras do opcoes_de do otimizar_gh.py).
using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Text.Json.Nodes;

namespace Otimiza.Cliente
{
    public sealed class Opcoes
    {
        readonly Dictionary<string, string> d = new Dictionary<string, string>();

        public static Opcoes De(IEnumerable<string?>? linhas)
        {
            var o = new Opcoes();
            foreach (var item in linhas ?? Enumerable.Empty<string?>())
            {
                if (item == null) continue;
                foreach (var linha0 in item.Replace("\r", "").Split('\n'))
                {
                    var linha = linha0.Split('#')[0];
                    int i = linha.IndexOf('=');
                    if (i < 0) continue;
                    o.d[linha.Substring(0, i).Trim().ToLowerInvariant()] = linha.Substring(i + 1).Trim();
                }
            }
            return o;
        }

        public IEnumerable<KeyValuePair<string, string>> Itens => d;
        public bool Tem(string k) => d.ContainsKey(k);

        public string Texto(string k, string padrao) =>
            d.TryGetValue(k, out var v) && v != "" ? v : padrao;

        public double Num(string k, double padrao) =>
            d.TryGetValue(k, out var v) && double.TryParse(v, NumberStyles.Float, CultureInfo.InvariantCulture, out var x)
                ? x : padrao;

        public int Int(string k, int padrao) =>
            d.TryGetValue(k, out var v) && int.TryParse(v, NumberStyles.Integer, CultureInfo.InvariantCulture, out var x)
                ? x : padrao;

        public bool Sim(string k, bool padrao = false)
        {
            if (!d.TryGetValue(k, out var v)) return padrao;
            return new[] { "sim", "s", "true", "1", "yes", "verdadeiro" }.Contains(v.Trim().ToLowerInvariant());
        }

        /// <summary>rho_&lt;rótulo&gt; e tau_vidro → {"fachada": {"rho": 0.5}, "vidro": {"tau": 0.64}}.</summary>
        public JsonObject Materiais()
        {
            var mat = new JsonObject();
            JsonObject De(string r)
            {
                if (mat[r] is JsonObject o) return o;
                var n = new JsonObject();
                mat[r] = n;
                return n;
            }
            foreach (var kv in d)
            {
                if (kv.Key.StartsWith("rho_"))
                    De(kv.Key.Substring(4))["rho"] = double.Parse(kv.Value, CultureInfo.InvariantCulture);
            }
            if (d.TryGetValue("tau_vidro", out var t))
                De("vidro")["tau"] = double.Parse(t, CultureInfo.InvariantCulture);
            return mat;
        }

        /// <summary>"0,0,-10" ou "0;0;-10" → [0, 0, -10].</summary>
        public double[] Vetor(string k, string padrao) =>
            Texto(k, padrao).Replace(";", ",").Split(',')
                .Select(s => double.Parse(s.Trim(), CultureInfo.InvariantCulture)).ToArray();
    }
}

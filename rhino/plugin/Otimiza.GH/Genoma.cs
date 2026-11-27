// Genoma = sliders e Value Lists ligados na entrada `genoma` (como o genoma_de do otimizar_gh.py).
using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Text.Json.Nodes;
using Grasshopper.GUI.Base;
using Grasshopper.Kernel;
using Grasshopper.Kernel.Special;
using Otimiza.Cliente;

namespace Otimiza.GH
{
    sealed class Gene
    {
        public string Nome = "";
        public string Tipo = "";
        public JsonObject Protocolo = new JsonObject();   // como vai no /iniciar
        public IGH_DocumentObject Objeto = null!;
        public JsonNode Original = 0;                       // valor antes de otimizar
    }

    static class Genoma
    {
        public static List<Gene> De(GH_Component comp)
        {
            var entrada = comp.Params.Input.FirstOrDefault(p => p.NickName == "genoma")
                          ?? throw new InvalidOperationException("componente sem a entrada 'genoma'");
            var genes = new List<Gene>();
            var nomes = new HashSet<string>();
            foreach (var src in entrada.Sources)
            {
                string nome = string.IsNullOrWhiteSpace(src.NickName) ? "p" : src.NickName.Trim();
                string b = nome;
                for (int k = 2; nomes.Contains(nome); k++) nome = b + "_" + k;
                nomes.Add(nome);
                var g = new Gene { Nome = nome, Objeto = src };
                var d = new JsonObject { ["nome"] = nome };
                if (src is GH_NumberSlider ns)
                {
                    var s = ns.Slider;
                    double lo = (double)s.Minimum, hi = (double)s.Maximum;
                    if (s.Type == GH_SliderAccuracy.Integer)
                    {
                        g.Tipo = "inteiro";
                        d["tipo"] = "inteiro"; d["minimo"] = (int)Math.Round(lo); d["maximo"] = (int)Math.Round(hi);
                    }
                    else if (s.Type == GH_SliderAccuracy.Even || s.Type == GH_SliderAccuracy.Odd)
                    {
                        int resto = s.Type == GH_SliderAccuracy.Even ? 0 : 1;
                        var ops = new JsonArray();
                        for (int i = (int)Math.Ceiling(lo); i <= (int)Math.Floor(hi); i++)
                            if (((i % 2) + 2) % 2 == resto) ops.Add(i.ToString(CultureInfo.InvariantCulture));
                        g.Tipo = "categorico";
                        d["tipo"] = "categorico"; d["opcoes"] = ops;
                    }
                    else
                    {
                        g.Tipo = "continuo";
                        d["tipo"] = "continuo"; d["minimo"] = lo; d["maximo"] = hi; d["casas"] = s.DecimalPlaces;
                    }
                    g.Original = (double)ns.CurrentValue;
                }
                else if (src is GH_ValueList vl)
                {
                    var ops = vl.ListItems.Select(it => it.Name).ToList();
                    if (ops.Count == 0) throw new InvalidOperationException("Value List '" + nome + "' vazia");
                    g.Tipo = "categorico";
                    d["tipo"] = "categorico";
                    d["opcoes"] = new JsonArray(ops.Select(o => (JsonNode)o).ToArray());
                    g.Original = vl.FirstSelectedItem?.Name ?? ops[0];
                }
                else
                    throw new InvalidOperationException("'" + nome + "' ligado em genoma não é slider nem Value List");
                g.Protocolo = d;
                genes.Add(g);
            }
            if (genes.Count == 0) throw new InvalidOperationException("ligue sliders ou Value Lists em 'genoma'");
            return genes;
        }

        static void Por(Gene g, JsonNode? v)
        {
            if (g.Objeto is GH_NumberSlider ns)
            {
                double x = J.Num(v);
                if (double.IsNaN(x)) throw new ArgumentException("valor inválido para '" + g.Nome + "'");
                ns.Slider.RaiseEvents = false;
                ns.SetSliderValue((decimal)x);
                ns.Slider.RaiseEvents = true;
                ns.ExpireSolution(false);
            }
            else if (g.Objeto is GH_ValueList vl)
            {
                string nome = J.Texto(v) ?? "";
                foreach (var it in vl.ListItems) it.Selected = it.Name == nome;
                vl.ExpireSolution(false);
            }
        }

        /// <summary>Põe os valores nos sliders/listas sem disparar soluções (ExpireSolution(false)).</summary>
        public static void Aplicar(IEnumerable<Gene> genes, JsonObject valores)
        {
            foreach (var g in genes) Por(g, valores[g.Nome]);
        }

        public static void Restaurar(IEnumerable<Gene> genes)
        {
            foreach (var g in genes) Por(g, g.Original);
        }
    }
}

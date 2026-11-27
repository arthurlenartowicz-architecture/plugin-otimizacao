// Componente "Otimizar" (C#): o mesmo do otimizar_gh.py, com as mesmas entradas e saídas.
// O Grasshopper gera a geometria de cada variante; o servidor Python avalia na GPU.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Text;
using System.Text.Json.Nodes;
using Grasshopper.Kernel;
using Grasshopper.Kernel.Types;
using Otimiza.Cliente;
using Rhino;

namespace Otimiza.GH
{
    public class OtimizarComponent : GH_Component
    {
        const string RaizPadrao = @"C:\Users\PC\Documents\plugin-otimizacao";
        static readonly string[] Outras = { "barras", "apoios", "cargas", "secao" };
        static List<string>? metricas;     // cache de GET /metricas
        LacoGH? laco;

        public OtimizarComponent()
            : base("Otimizar", "Otimizar",
                   "O Grasshopper gera a geometria; o motor em Python avalia na GPU e otimiza (protocolo v1).\n" +
                   "Ligue em genoma os sliders e Value Lists; a geometria nas entradas por rótulo; " +
                   "escreva o problema e aperte iniciar. Esc ou parar interrompem.",
                   "Otimiza", "Otimização")
        { }

        public override Guid ComponentGuid => new Guid("c3a1f6d2-8b4e-4a0f-9e57-2d6b1f0a7c93");
        public override GH_Exposure Exposure => GH_Exposure.primary;

        protected override void RegisterInputParams(GH_InputParamManager p)
        {
            void Lista(string nome, string desc) => p[p.AddGenericParameter(nome, nome, desc, GH_ParamAccess.list)].Optional = true;
            void Textos(string nome, string desc) => p[p.AddTextParameter(nome, nome, desc, GH_ParamAccess.list)].Optional = true;
            void Botao(string nome) => p[p.AddBooleanParameter(nome, nome, "botão", GH_ParamAccess.item)].Optional = true;
            Lista("genoma", "sliders (contínuo/inteiro) e Value Lists (categórico) que o otimizador mexe; nome = NickName");
            foreach (var r in Geometria.Projeto) Lista(r, "geometria do projeto: " + r + " (Brep, Mesh, Surface, Extrusion)");
            foreach (var r in Geometria.Fixos) Lista(r, "geometria fixa (mandada uma vez só): " + r);
            Lista("barras", "linhas/curvas das barras (estrutura)");
            Lista("apoios", "pontos de apoio");
            Lista("cargas", "pontos com carga (força em opcoes: forca = 0,0,-10)");
            Textos("secao", "seção do catálogo TUBOS (ex. RO114.3/4): uma ou uma por barra");
            Textos("problema", "uma linha por objetivo/restrição: max sol_luz.da, sol_luz.ase, sol_luz.udi >= 50");
            Textos("opcoes", "chave = valor (populacao, geracoes, nivel, caso, malha, lote, modo, porta…)");
            Botao("iniciar");
            Botao("parar");
            Botao("avaliar");
        }

        protected override void RegisterOutputParams(GH_OutputParamManager p)
        {
            p.AddTextParameter("log", "log", "mensagens", GH_ParamAccess.item);
            p.AddTextParameter("progresso", "progresso", "geração e lote", GH_ParamAccess.item);
            p.AddTextParameter("metricas", "metricas", "todas as métricas do servidor", GH_ParamAccess.list);
            p.AddTextParameter("valores", "valores", "métricas da variante atual (botão avaliar)", GH_ParamAccess.list);
        }

        // ------------------------------------------------------------ entradas
        static List<object?> Objetos(IGH_DataAccess DA, string nome)
        {
            var l = new List<IGH_Goo>();
            DA.GetDataList(nome, l);
            return l.Select(g => g?.ScriptVariable()).ToList();
        }

        static List<string> Textos(IGH_DataAccess DA, string nome)
        {
            var l = new List<string>();
            DA.GetDataList(nome, l);
            return l.Where(s => s != null).ToList();
        }

        static bool Botao(IGH_DataAccess DA, string nome)
        {
            bool b = false;
            DA.GetData(nome, ref b);
            return b;
        }

        static Dictionary<string, List<object?>> Entradas(IGH_DataAccess DA)
        {
            var d = new Dictionary<string, List<object?>>();
            foreach (var r in Geometria.Projeto.Concat(Geometria.Fixos).Concat(Outras))
                d[r] = r == "secao" ? Textos(DA, r).Cast<object?>().ToList() : Objetos(DA, r);
            return d;
        }

        // ------------------------------------------------------------ servidor e pasta do projeto
        string Raiz(Opcoes op)
        {
            if (op.Tem("raiz")) return op.Texto("raiz", RaizPadrao);
            var candidatos = new List<string?>();
            try { candidatos.Add(Grasshopper.Instances.ComponentServer.FindAssemblyByObject(this)?.Location); } catch { }
            candidatos.Add(OnPingDocument()?.FilePath);
            foreach (var c in candidatos)
            {
                if (string.IsNullOrEmpty(c)) continue;
                for (var dir = Path.GetDirectoryName(c); !string.IsNullOrEmpty(dir); dir = Path.GetDirectoryName(dir))
                    if (File.Exists(Path.Combine(dir, "CLAUDE.md")) && Directory.Exists(Path.Combine(dir, "src", "otimiza")))
                        return dir;
            }
            return RaizPadrao;
        }

        static void SubirServidor(string raiz, int porta)
        {
            var info = new ProcessStartInfo(Path.Combine(raiz, ".venv", "Scripts", "python.exe"),
                                            "-m otimiza.servidor --porta " + porta)
            {
                WorkingDirectory = raiz, UseShellExecute = false, CreateNoWindow = false,
            };
            info.Environment["PYTHONPATH"] = Path.Combine(raiz, "src");
            info.Environment["PYTHONIOENCODING"] = "utf-8";
            Process.Start(info);
        }

        static JsonArray Arr(IEnumerable<string> s) => new JsonArray(s.Select(x => (JsonNode)x).ToArray());

        static JsonObject PedidoBase(Opcoes op, IEnumerable<string> problema)
        {
            var d = new JsonObject
            {
                ["versao"] = ClienteProtocolo.Versao, ["problema"] = Arr(problema),
                ["nivel"] = op.Texto("nivel", "rapido"),
                ["escala"] = RhinoMath.UnitScale(RhinoDoc.ActiveDoc.ModelUnitSystem, UnitSystem.Meters),
                ["materiais"] = op.Materiais(),
            };
            if (op.Tem("epw")) d["epw"] = op.Texto("epw", "");
            return d;
        }

        static string Junto(IEnumerable<string> log) => string.Join("\n", log);

        // ------------------------------------------------------------ SolveInstance
        protected override void SolveInstance(IGH_DataAccess DA)
        {
            // 1) chamado pelo próprio laço: só captura a geometria desta variante
            if (laco != null && laco.Capturando)
            {
                try { laco.Captura = new Gerado(Geometria.Atual(Entradas(DA), laco.Op)); }
                catch (Exception ex) { laco.Captura = new Gerado(null, ex.Message); }
                DA.SetData("log", Junto(laco.Ciclo.Log));
                DA.SetData("progresso", laco.Ciclo.Progresso());
                return;
            }
            string? log = null;
            try
            {
                log = Resolver(DA);
            }
            catch (Exception ex)
            {
                AddRuntimeMessage(GH_RuntimeMessageLevel.Error, ex.Message);
                log = "ERRO:\n" + ex;
            }
            if (log == null)
                log = laco != null ? Junto(laco.Ciclo.Log)
                    : "Ligue os sliders em 'genoma', a geometria nas entradas por rótulo, escreva o problema e aperte 'iniciar'.";
            DA.SetData("log", log);
            if (laco != null) DA.SetData("progresso", laco.Ciclo.Ativo ? laco.Ciclo.Progresso() : laco.Ciclo.Estado);
        }

        string? Resolver(IGH_DataAccess DA)
        {
            var op = Opcoes.De(Textos(DA, "opcoes"));
            int porta = op.Int("porta", 8765);
            bool rodando = laco != null && laco.Ciclo.Ativo;
            string? log = null;
            if (Botao(DA, "parar") && rodando)
            {
                laco!.Parar("botão parar");
                return Junto(laco.Ciclo.Log);
            }
            bool iniciar = Botao(DA, "iniciar") && !rodando;
            if (iniciar && op.Texto("modo", "").ToLowerInvariant() == "trabalhador")
            {
                laco = new LacoGH(this, op, null, Genoma.De(this), null);   // mesmos sliders do principal
            }
            else if (iniciar)
            {
                var genes = Genoma.De(this);
                var problema = Textos(DA, "problema");
                if (problema.Count == 0) throw new InvalidOperationException("escreva o problema (Panel ligado em 'problema')");
                string raiz = Raiz(op), caso = op.Texto("caso", "gh");
                var pedido = PedidoBase(op, problema);
                pedido["parametros"] = new JsonArray(genes.Select(g => g.Protocolo.DeepClone()).ToArray());
                pedido["algoritmo"] = op.Texto("algoritmo", "auto");
                pedido["populacao"] = op.Int("populacao", 48);
                pedido["geracoes"] = op.Int("geracoes", 20);
                pedido["semente"] = op.Int("semente", 0);
                pedido["caso"] = caso;
                pedido["saida"] = Path.Combine(raiz, "resultados", caso);
                pedido["cliente"] = new JsonObject
                {
                    ["genoma"] = new JsonArray(genes.Select(g => (JsonNode)new JsonObject
                    {
                        ["nome"] = g.Nome, ["guid"] = g.Objeto.InstanceGuid.ToString(), ["tipo"] = g.Tipo,
                    }).ToArray()),
                    ["arquivo_gh"] = OnPingDocument()?.FilePath,
                    ["componente"] = "Otimiza.gha " + new OtimizaInfo().Version,
                };
                var fixas = Geometria.Malhas(Entradas(DA), Geometria.Fixos, Geometria.Parametros(op.Texto("malha", "minima")));
                if (fixas.Count > 0) pedido["fixas"] = new JsonObject { ["malhas"] = fixas };
                laco = new LacoGH(this, op, pedido, genes, () => SubirServidor(raiz, porta));
            }
            var valores = new List<string>();
            if (Botao(DA, "avaliar")) log = Avaliar(DA, op, porta, valores);
            if (valores.Count > 0) DA.SetDataList("valores", valores);

            using var cli = new ClienteProtocolo(porta);
            if (cli.Vivo())
            {
                if (metricas == null)
                {
                    metricas = new List<string>();
                    if (cli.Metricas()["metricas"] is JsonObject m)
                        foreach (var mod in m.OrderBy(kv => kv.Key, StringComparer.Ordinal))
                            foreach (var kv in ((JsonObject)mod.Value!).OrderBy(kv => kv.Key, StringComparer.Ordinal))
                                metricas.Add(mod.Key + "." + kv.Key + "  [" + J.Texto(kv.Value!["unidade"]) + ", "
                                             + J.Texto(kv.Value!["sentido"]) + "]  " + J.Texto(kv.Value!["descricao"]));
                }
                DA.SetDataList("metricas", metricas);
            }
            return log;
        }

        /// <summary>Avalia a variante do canvas (sem otimizar) e grava a referência da validação.</summary>
        string Avaliar(IGH_DataAccess DA, Opcoes op, int porta, List<string> valores)
        {
            string raiz = Raiz(op);
            using var cli = new ClienteProtocolo(porta);
            if (!cli.Vivo())
            {
                SubirServidor(raiz, porta);
                return "Subindo o servidor… aperte 'avaliar' de novo em ~20 s.";
            }
            var entradas = Entradas(DA);
            var sw = Stopwatch.StartNew();
            var geo = Geometria.Atual(entradas, op);
            double tGh = sw.Elapsed.TotalSeconds;
            var pedido = PedidoBase(op, Textos(DA, "problema"));
            pedido["geometrias"] = new JsonArray(geo);
            var fixas = Geometria.Malhas(entradas, Geometria.Fixos, Geometria.Parametros(op.Texto("malha", "minima")));
            if (fixas.Count > 0) pedido["fixas"] = new JsonObject { ["malhas"] = fixas };
            var r = cli.Avaliar(pedido);
            if (r["valores"] is JsonObject vs)
                foreach (var kv in vs.OrderBy(kv => kv.Key, StringComparer.Ordinal))
                    valores.Add(kv.Key + " = " + J.Texto((kv.Value as JsonArray)?[0]));
            var genoma = new JsonObject();
            try { foreach (var g in Genoma.De(this)) genoma[g.Nome] = g.Original.DeepClone(); } catch { }
            string pasta = Path.Combine(raiz, "testes", "referencias", "gh");
            Directory.CreateDirectory(pasta);
            string arq = Path.Combine(pasta, "variante_" + op.Texto("caso", "gh") + ".json");
            var refe = (JsonObject)pedido.DeepClone();
            refe["genoma"] = genoma;
            refe["valores_gh"] = r["valores"]?.DeepClone();
            refe["tempo_gh_s"] = tGh;
            File.WriteAllText(arq, refe.ToJsonString(), new UTF8Encoding(false));   // escapa acentos: ASCII puro
            return string.Format(System.Globalization.CultureInfo.InvariantCulture,
                                 "Avaliado em {0:0.00} s (GH {1:0} ms para malhar e converter). Gravado {2}",
                                 J.Num(r["tempo_s"], 0), 1000 * tGh, arq);
        }
    }
}

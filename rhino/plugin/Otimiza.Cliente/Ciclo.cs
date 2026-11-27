// O laço do cliente, sem Rhino: o mesmo da classe Laco do otimizar_gh.py.
// Quem usa (o componente do GH, ou o programa de teste) chama Passo() num timer e
// fornece `gerar` (parâmetros → geometria do protocolo).
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Linq;
using System.Text.Json.Nodes;

namespace Otimiza.Cliente
{
    public sealed class Tarefa
    {
        public JsonNode Id = JsonValue.Create("")!;
        public JsonObject Parametros = new JsonObject();
    }

    /// <summary>Geometria de uma variante, ou o erro (vira inviável no servidor).</summary>
    public readonly struct Gerado
    {
        public readonly JsonNode? Geometria;
        public readonly string? Erro;
        public Gerado(JsonNode? geometria, string? erro = null) { Geometria = geometria; Erro = erro; }
    }

    /// <summary>pedido = null → modo trabalhador: não inicia nada; espera uma otimização no
    /// servidor (aberta por outro cliente), pega tarefas da mesma fila e, quando ela acaba,
    /// espera a próxima, até Parar().</summary>
    public sealed class Ciclo
    {
        public ClienteProtocolo Cli { get; }
        readonly JsonObject? pedido;
        readonly HashSet<string> nomes;
        readonly Func<Tarefa, Gerado> gerar;

        public int Lote = 4;                 // pequeno: divide melhor entre vários Rhinos
        public double EsperaGpu_s = 0.02;    // GPU leva ~65 ms/geração; consulta ~1 ms
        public double LimiteConexao_s = 120;

        public string Estado { get; private set; } = "conectando";
        public bool Trabalhador => pedido == null;
        public bool Ativo => Estado == "conectando" || Estado == "rodando";
        public string? Rodada { get; private set; }
        public JsonObject Ultimo { get; private set; } = new JsonObject();
        public readonly List<string> Log = new List<string>();
        public readonly List<double> Tempos = new List<double>();   // s por variante (gerar)
        public int Gerados { get; private set; }
        public int Devolvidas { get; private set; }
        public int Pendentes => fila.Count;

        /// <summary>Uma linha nova no log.</summary>
        public event Action<string>? AoEscrever;
        /// <summary>Acabou (fim, erro ou parado): o GH restaura os sliders.</summary>
        public event Action<string>? AoTerminar;
        /// <summary>Trabalhador: a otimização acabou, voltou a esperar a próxima.</summary>
        public event Action? AoEsperar;

        readonly Stopwatch relogio = Stopwatch.StartNew();
        double Agora => relogio.Elapsed.TotalSeconds;
        double tEspera;
        double tInicio;
        string? visto;
        readonly List<Tarefa> fila = new List<Tarefa>();
        readonly List<JsonObject> prontos = new List<JsonObject>();

        public Ciclo(ClienteProtocolo cli, JsonObject? pedido, IEnumerable<string> nomes,
                     Func<Tarefa, Gerado> gerar, Action? subirServidor = null)
        {
            Cli = cli;
            this.pedido = pedido;
            this.nomes = new HashSet<string>(nomes);
            this.gerar = gerar;
            tInicio = Agora;
            if (Trabalhador)
                Escrever("Modo trabalhador: esperando uma otimização no servidor (" + cli.Base + ").");
            else if (subirServidor != null && !cli.Vivo())
            {
                subirServidor();
                Escrever("Subindo o servidor (1ª vez carrega a GPU, ~10-20 s)…");
            }
        }

        public void Escrever(string txt)
        {
            Log.Add(txt);
            if (Log.Count > 30) Log.RemoveRange(0, Log.Count - 30);
            AoEscrever?.Invoke(txt);
        }

        void Ver(string desc)
        {
            if (desc == visto) return;
            visto = desc;
            if (Trabalhador) Escrever("Trabalhador vê: " + desc);
        }

        // ------------------------------------------------------------ interface
        public string Progresso()
        {
            var u = Ultimo;
            string pre = Trabalhador ? "trabalhador · " : "";
            if (Estado == "conectando" || u.Count == 0)
            {
                if (Trabalhador)   // contador: mostra que o timer está rodando
                    return pre + (visto ?? "esperando") + " · " + (int)(Agora - tInicio) + " s";
                return Estado;
            }
            if (!Ativo) return Estado;
            int g = J.Int(u["geracao"]), G = J.Int(u["geracoes"], 1);
            string txt = pre + "ger " + Math.Min(g + 1, G) + "/" + G;
            int lote = J.Int(u["lote"]);
            if (J.Texto(u["estado"]) == "avaliando") txt += " · GPU";
            else if (lote > 0) txt += " · " + (lote - J.Int(u["faltam"])) + "/" + lote;
            return txt;
        }

        /// <summary>Fração feita (0–1), para a barra de progresso.</summary>
        public double Fracao()
        {
            var u = Ultimo;
            int G = Math.Max(J.Int(u["geracoes"], 1), 1), lote = Math.Max(J.Int(u["lote"], 1), 1);
            double feito = J.Int(u["geracao"]) + (lote - J.Int(u["faltam"], lote)) / (double)lote;
            return Math.Min(feito / G, 1.0);
        }

        // ------------------------------------------------------------ timer
        /// <summary>Um passo do laço; gera tarefas por no máximo `bloco_s` e devolve a vez.</summary>
        public void Passo(double bloco_s = 0.25)
        {
            if (!Ativo) return;
            try
            {
                if (Estado == "conectando") Conectar();
                else Trabalhar(bloco_s);
            }
            catch (Exception ex)
            {
                string pilha = ex.StackTrace ?? "";
                Escrever("ERRO: " + ex.Message + "\n" + (pilha.Length > 1500 ? pilha.Substring(pilha.Length - 1500) : pilha));
                Finalizar("erro");
            }
        }

        void Conectar()
        {
            if (!Trabalhador && Agora - tInicio > LimiteConexao_s)
                throw new ErroServidor("o servidor não respondeu em " + LimiteConexao_s + " s (veja a janela de console)");
            if (Agora < tEspera) return;
            if (!Cli.Vivo())
            {
                tEspera = Agora + (Trabalhador ? 1.0 : 0.5);
                Ver("servidor fora do ar");
                return;
            }
            JsonObject r;
            if (Trabalhador)
            {
                r = Cli.Estado();
                string est = J.Texto(r["estado"]) ?? "?";
                Ver("servidor: " + est);
                if (est != "gerando" && est != "avaliando")
                {
                    tEspera = Agora + 1.0;     // nenhuma otimização em andamento
                    return;
                }
                var deles = (r["parametros"] as JsonArray)?.Select(n => J.Texto(n) ?? "") ?? Enumerable.Empty<string>();
                var faltam = deles.Where(n => !nomes.Contains(n)).ToList();
                if (faltam.Count > 0)
                    throw new InvalidOperationException("a otimização usa parâmetros que este Rhino não tem no genoma: "
                                                        + string.Join(", ", faltam) + " (abra a mesma definição)");
                Rodada = J.Texto(r["rodada"]);
                Escrever("Entrou na otimização (rodada " + Rodada + ", geração " + J.Texto(r["geracao"]) + ").");
            }
            else
            {
                r = Cli.Iniciar(pedido!);
                Rodada = J.Texto(r["rodada"]);
                Escrever("Otimização iniciada: " + J.Texto(r["algoritmo"]) + ", " + J.Texto(pedido!["populacao"])
                         + " × " + J.Texto(pedido!["geracoes"]) + ".");
            }
            Ultimo = r;
            Estado = "rodando";
        }

        void Trabalhar(double bloco_s)
        {
            if (Agora < tEspera) return;
            double tFim = Agora + bloco_s;      // bloco curto: depois devolve a vez à interface
            if (fila.Count == 0)
            {
                JsonObject r;
                try
                {
                    r = Cli.Tarefas(Lote);
                }
                catch (ErroServidor)
                {
                    if (!Trabalhador) throw;
                    EsperarProxima("servidor sem otimização");
                    return;
                }
                Ultimo = r;
                string est = J.Texto(r["estado"]) ?? "";
                if (est == "fim" || est == "erro") { Terminou(r); return; }
                foreach (var n in (r["tarefas"] as JsonArray) ?? new JsonArray())
                {
                    if (n is JsonObject t)
                        fila.Add(new Tarefa { Id = t["id"]!.DeepClone(), Parametros = (JsonObject)t["parametros"]!.DeepClone() });
                }
                if (fila.Count == 0)            // GPU avaliando: espera sem ocupar a interface
                {
                    tEspera = Agora + EsperaGpu_s;
                    return;
                }
            }
            while (fila.Count > 0 && Agora < tFim)
            {
                var t = fila[0];
                fila.RemoveAt(0);
                prontos.Add(Gerar(t));
            }
            if (prontos.Count > 0 && (fila.Count == 0 || Agora >= tFim))
            {
                var r = EnviarProntos();
                var erros = r["erros"] as JsonArray;
                if (erros != null && erros.Count > 0)
                    Escrever("Variantes com erro (viram inviáveis): " + string.Join("; ", erros.Take(3).Select(J.Texto)));
                string est = J.Texto(r["estado"]) ?? "";
                if (est == "fim" || est == "erro") Terminou(r);
            }
        }

        JsonObject EnviarProntos()
        {
            var lista = new JsonArray();
            foreach (var p in prontos) lista.Add(p);
            prontos.Clear();
            var r = Cli.Resultados(lista);
            Ultimo = r;
            return r;
        }

        JsonObject Gerar(Tarefa t)
        {
            var sw = Stopwatch.StartNew();
            Gerado g;
            try { g = gerar(t); }
            catch (Exception ex) { g = new Gerado(null, ex.Message); }
            double dt = sw.Elapsed.TotalSeconds;
            Tempos.Add(dt);
            Gerados++;
            var item = new JsonObject { ["id"] = t.Id.DeepClone(), ["tempo_s"] = dt };
            if (g.Erro != null || g.Geometria == null)
                item["erro"] = g.Erro ?? "componente não recalculou";
            else
                item["geometria"] = g.Geometria;
            return item;
        }

        // ------------------------------------------------------------ fim
        void Terminou(JsonObject r)
        {
            string est = J.Texto(r["estado"]) ?? "fim";
            if (est == "erro") Escrever("ERRO no servidor:\n" + J.Texto(r["erro"]));
            if (Trabalhador) { EsperarProxima("otimização terminou"); return; }
            Finalizar(est);
        }

        string TempoMedio() =>
            Tempos.Count == 0 ? "?" : (1000 * Tempos.Average()).ToString("0.0", System.Globalization.CultureInfo.InvariantCulture);

        void EsperarProxima(string motivo)
        {
            fila.Clear();
            prontos.Clear();
            Estado = "conectando";
            tEspera = Agora + 1.0;
            if (Tempos.Count > 0)
                Escrever(motivo + " (rodada " + Rodada + "). Tempo no GH: " + TempoMedio() + " ms/variante ("
                         + Tempos.Count + " variantes). Esperando a próxima.");
            Tempos.Clear();
            AoEsperar?.Invoke();
        }

        public void Parar(string motivo = "parado")
        {
            if (!Ativo) return;
            if (Trabalhador)
            {
                try   // entrega o que já gerou e devolve o resto para os outros Rhinos
                {
                    if (prontos.Count > 0) EnviarProntos();
                    if (fila.Count > 0)
                    {
                        var ids = new JsonArray();
                        foreach (var t in fila) ids.Add(t.Id.DeepClone());
                        var r = Cli.Devolver(ids);
                        Devolvidas += J.Int(r["devolvidas"]);
                    }
                }
                catch (Exception ex)
                {
                    Escrever("não consegui devolver as tarefas: " + ex.Message);
                }
                fila.Clear();
                prontos.Clear();
                Escrever("Trabalhador parado (" + motivo + "); a otimização segue nos outros Rhinos.");
                Finalizar("parado");
                return;
            }
            try { Ultimo = Cli.Parar(); }
            catch (Exception ex) { Escrever("não consegui avisar o servidor: " + ex.Message); }
            Escrever("Interrompido (" + motivo + "); o que já foi avaliado foi gravado.");
            Finalizar("parado");
        }

        void Finalizar(string estado)
        {
            Estado = estado;
            var arq = J.Texto(Ultimo["arquivo"]);
            if (!string.IsNullOrEmpty(arq)) Escrever("Resultado: " + arq);
            if (Tempos.Count > 0)
            {
                double gpu = J.Num(Ultimo["tempo_gpu_s_por_variante"]);
                Escrever("Tempo no GH: " + TempoMedio() + " ms/variante (" + Tempos.Count + " variantes); GPU: "
                         + (double.IsNaN(gpu) ? "?" : (1000 * gpu).ToString("0.00", System.Globalization.CultureInfo.InvariantCulture))
                         + " ms/variante.");
            }
            AoTerminar?.Invoke(estado);
        }
    }
}

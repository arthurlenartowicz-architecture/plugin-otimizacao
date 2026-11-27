// Janela de Otimização (4f2), em Eto.Forms: à esquerda o problema (métricas do servidor,
// objetivos, restrições, nível, população, gerações, algoritmo) e à direita os gráficos
// (WebView com plotly.js, rhino/plugin/web/). Não depende do Grasshopper: quem abre a
// janela (o componente Otimizar; depois o painel do Rhino, 4f4) implementa IAnfitriao.
using System;
using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text.Json.Nodes;
using Eto.Drawing;
using Eto.Forms;
using Otimiza.Cliente;

namespace Otimiza.UI
{
    public interface IAnfitriao
    {
        Configuracao Config { get; }
        /// <summary>A configuração mudou na janela (salvar com o documento).</summary>
        void ConfigMudou();
        /// <summary>Linhas do painel 'problema' ligado no componente (vazio = a janela manda).</summary>
        IList<string> ProblemaDoPainel { get; }
        /// <summary>Chaves do painel 'opcoes' que sobrescrevem a janela (nivel, populacao…).</summary>
        IEnumerable<string> OpcoesDoPainel { get; }
        int Porta { get; }
        bool Rodando { get; }
        /// <summary>resultados/&lt;caso&gt;/resultado.json (mostrado quando o servidor está livre).</summary>
        string? ArquivoResultado { get; }
        string Titulo { get; }
        void Iniciar();
        void Parar();
        void Aplicar(JsonObject parametros);
        void LigarServidor();
    }

    sealed class LinhaMetrica
    {
        public Metrica M = new Metrica();
        public bool Usar;
        public string Sentido = "min";
    }

    sealed class LinhaRestricao
    {
        public string Chave = "";
        public string Operador = "<=";
        public string Valor = "0";
    }

    public sealed class JanelaOtimizacao : Form
    {
        readonly IAnfitriao host;
        readonly ClienteProtocolo cli;
        readonly WebView web = new WebView();
        readonly GridView gradeMetricas = new GridView { ShowHeader = true, AllowMultipleSelection = false };
        readonly GridView gradeRestricoes = new GridView { ShowHeader = true, Height = 110 };
        readonly ObservableCollection<LinhaMetrica> metricas = new ObservableCollection<LinhaMetrica>();
        readonly ObservableCollection<LinhaRestricao> restricoes = new ObservableCollection<LinhaRestricao>();
        readonly DropDown nivel = new DropDown();
        readonly DropDown algoritmo = new DropDown();
        readonly NumericStepper populacao = new NumericStepper { MinValue = 2, MaxValue = 100000, DecimalPlaces = 0 };
        readonly NumericStepper geracoes = new NumericStepper { MinValue = 1, MaxValue = 100000, DecimalPlaces = 0 };
        readonly Label efetivo = new Label();
        readonly Label avisos = new Label { TextColor = Color.FromArgb(160, 60, 0), Wrap = WrapMode.Word };
        readonly Label status = new Label { Wrap = WrapMode.Word };
        readonly Button botaoIniciar = new Button { Text = "Iniciar" };
        readonly Button botaoParar = new Button { Text = "Parar" };
        readonly Button botaoServidor = new Button { Text = "Ligar servidor", Visible = false };
        readonly UITimer timer = new UITimer { Interval = 0.5 };
        List<Metrica> catalogo = new List<Metrica>();
        bool paginaPronta, carregando, vivo;
        string ultimoEnviado = "";
        DateTime proximaChecagem = DateTime.MinValue;

        public JanelaOtimizacao(IAnfitriao host, string pastaWeb)
        {
            this.host = host;
            cli = new ClienteProtocolo(host.Porta);
            Title = "Otimização — " + host.Titulo;
            ClientSize = new Size(1320, 820);
            MinimumSize = new Size(900, 560);
            Resizable = true;
            Content = new Splitter
            {
                Orientation = Orientation.Horizontal, Position = 430, FixedPanel = SplitterFixedPanel.Panel1,
                Panel1 = Esquerda(), Panel2 = web,
            };
            web.MessageReceived += (s, e) => Mensagem(e.Message);
            web.DocumentTitleChanged += (s, e) =>             // alternativa sem window.eto
            {
                var t = e.Title ?? "";
                if (!t.StartsWith("otimiza:")) return;
                int i = t.IndexOf(':', 8);
                if (i > 0) Mensagem(t.Substring(i + 1));
            };
            var pagina = Path.Combine(pastaWeb, "janela.html");
            if (File.Exists(pagina)) web.Url = new Uri(pagina);
            else web.LoadHtml("<p style='font-family:sans-serif'>Não achei " + pagina +
                              " (compile o Otimiza.GH: a pasta web vai junto do .gha).</p>");
            CarregarConfig();
            timer.Elapsed += (s, e) => Consultar();
            timer.Start();
            Closed += (s, e) => { timer.Stop(); cli.Dispose(); };
            Consultar();
        }

        // ------------------------------------------------------------ lado esquerdo
        Control Esquerda()
        {
            gradeMetricas.DataStore = metricas;
            gradeMetricas.Columns.Add(new GridColumn
            {
                HeaderText = "usar", Editable = true, Width = 44,
                DataCell = new CheckBoxCell { Binding = Binding.Delegate<LinhaMetrica, bool?>(r => r.Usar, (r, v) => r.Usar = v ?? false) },
            });
            gradeMetricas.Columns.Add(new GridColumn
            {
                HeaderText = "métrica", Width = 190,
                DataCell = new TextBoxCell { Binding = Binding.Delegate<LinhaMetrica, string>(r => r.M.Chave) },
            });
            gradeMetricas.Columns.Add(new GridColumn
            {
                HeaderText = "sentido", Editable = true, Width = 62,
                DataCell = new ComboBoxCell
                {
                    DataStore = new[] { "max", "min" },
                    Binding = Binding.Delegate<LinhaMetrica, object>(r => r.Sentido, (r, v) => r.Sentido = v as string ?? r.Sentido),
                },
            });
            gradeMetricas.Columns.Add(new GridColumn
            {
                HeaderText = "unidade", Width = 80,
                DataCell = new TextBoxCell { Binding = Binding.Delegate<LinhaMetrica, string>(r => r.M.Unidade) },
            });
            gradeMetricas.Columns.Add(new GridColumn
            {
                HeaderText = "descrição", Width = 260,
                DataCell = new TextBoxCell { Binding = Binding.Delegate<LinhaMetrica, string>(r => r.M.Descricao) },
            });
            gradeMetricas.CellEdited += (s, e) => SalvarConfig();

            gradeRestricoes.DataStore = restricoes;
            gradeRestricoes.Columns.Add(new GridColumn
            {
                HeaderText = "métrica", Editable = true, Width = 200,
                DataCell = new ComboBoxCell
                {
                    Binding = Binding.Delegate<LinhaRestricao, object>(r => r.Chave, (r, v) => r.Chave = v as string ?? r.Chave),
                },
            });
            gradeRestricoes.Columns.Add(new GridColumn
            {
                HeaderText = "", Editable = true, Width = 50,
                DataCell = new ComboBoxCell
                {
                    DataStore = new[] { "<=", ">=" },
                    Binding = Binding.Delegate<LinhaRestricao, object>(r => r.Operador, (r, v) => r.Operador = v as string ?? r.Operador),
                },
            });
            gradeRestricoes.Columns.Add(new GridColumn
            {
                HeaderText = "valor", Editable = true, Width = 80,
                DataCell = new TextBoxCell { Binding = Binding.Delegate<LinhaRestricao, string>(r => r.Valor, (r, v) => r.Valor = v ?? "0") },
            });
            gradeRestricoes.CellEdited += (s, e) => SalvarConfig();
            var mais = new Button { Text = "+ restrição" };
            mais.Click += (s, e) =>
            {
                restricoes.Add(new LinhaRestricao { Chave = catalogo.FirstOrDefault()?.Chave ?? "" });
                SalvarConfig();
            };
            var menos = new Button { Text = "− remover" };
            menos.Click += (s, e) =>
            {
                if (gradeRestricoes.SelectedItem is LinhaRestricao r) { restricoes.Remove(r); SalvarConfig(); }
            };

            foreach (var n in Configuracao.Niveis) nivel.Items.Add(n);
            foreach (var a in Configuracao.Algoritmos) algoritmo.Items.Add(a);
            nivel.SelectedIndexChanged += (s, e) => SalvarConfig();
            algoritmo.SelectedIndexChanged += (s, e) => SalvarConfig();
            populacao.ValueChanged += (s, e) => SalvarConfig();
            geracoes.ValueChanged += (s, e) => SalvarConfig();

            botaoIniciar.Click += (s, e) => Iniciar();
            botaoParar.Click += (s, e) => host.Parar();
            botaoServidor.Click += (s, e) => { host.LigarServidor(); status.Text = "Subindo o servidor (~20 s)…"; };
            var ultimo = new Button { Text = "Mostrar último resultado" };
            ultimo.Click += (s, e) => { ultimoEnviado = ""; MostrarArquivo(); };

            var config = new TableLayout
            {
                Spacing = new Size(6, 4),
                Rows =
                {
                    new TableRow(new Label { Text = "nível" }, nivel),
                    new TableRow(new Label { Text = "população" }, populacao),
                    new TableRow(new Label { Text = "gerações" }, geracoes),
                    new TableRow(new Label { Text = "algoritmo" }, algoritmo),
                    new TableRow(null, efetivo),
                },
            };
            return new TableLayout
            {
                Padding = 8, Spacing = new Size(4, 6),
                Rows =
                {
                    new Label { Text = "Objetivos: marque 1 ou mais e escolha min/max", Font = SystemFonts.Bold() },
                    new TableRow(gradeMetricas) { ScaleHeight = true },
                    new Label { Text = "Restrições (o projeto só vale se cumprir)", Font = SystemFonts.Bold() },
                    gradeRestricoes,
                    new StackLayout { Orientation = Orientation.Horizontal, Spacing = 4, Items = { mais, menos } },
                    new GroupBox { Text = "Configuração", Content = config },
                    avisos,
                    new StackLayout { Orientation = Orientation.Horizontal, Spacing = 6,
                                      Items = { botaoIniciar, botaoParar, botaoServidor, ultimo } },
                    status,
                },
            };
        }

        // ------------------------------------------------------------ configuração
        void CarregarConfig()
        {
            carregando = true;
            try
            {
                var c = host.Config;
                nivel.SelectedKey = c.Nivel;
                algoritmo.SelectedKey = c.Algoritmo;
                populacao.Value = c.Populacao;
                geracoes.Value = c.Geracoes;
                restricoes.Clear();
                foreach (var r in c.Restricoes)
                    restricoes.Add(new LinhaRestricao
                    {
                        Chave = r.Chave, Operador = r.Operador, Valor = r.Valor.ToString("R", CultureInfo.InvariantCulture),
                    });
                MontarMetricas();
            }
            finally { carregando = false; }
            Avisos();
        }

        void MontarMetricas()
        {
            var c = host.Config;
            metricas.Clear();
            var chaves = new HashSet<string>(catalogo.Select(m => m.Chave));
            foreach (var m in catalogo)
            {
                var o = c.Objetivos.FirstOrDefault(x => x.Chave == m.Chave);
                metricas.Add(new LinhaMetrica { M = m, Usar = o != null, Sentido = o?.Sentido ?? m.Sentido });
            }
            foreach (var o in c.Objetivos.Where(o => !chaves.Contains(o.Chave)))   // servidor sem o módulo
                metricas.Add(new LinhaMetrica
                {
                    M = new Metrica { Chave = o.Chave, Descricao = "(não registrada no servidor)" },
                    Usar = true, Sentido = o.Sentido,
                });
            if (gradeRestricoes.Columns[0].DataCell is ComboBoxCell cb)
                cb.DataStore = catalogo.Select(m => m.Chave).ToList();
        }

        void SalvarConfig()
        {
            if (carregando) return;
            var c = host.Config;
            // objetivos na ordem em que já estavam; novos no fim
            var marcadas = metricas.Where(r => r.Usar).ToList();
            var antes = c.Objetivos.Select(o => o.Chave).ToList();
            c.Objetivos = marcadas.OrderBy(r => antes.IndexOf(r.M.Chave) < 0 ? int.MaxValue : antes.IndexOf(r.M.Chave))
                                  .Select(r => new Objetivo { Chave = r.M.Chave, Sentido = r.Sentido }).ToList();
            c.Restricoes = restricoes.Where(r => !string.IsNullOrWhiteSpace(r.Chave)).Select(r => new Restricao
            {
                Chave = r.Chave, Operador = r.Operador,
                Valor = double.TryParse(r.Valor.Replace(',', '.'), NumberStyles.Float, CultureInfo.InvariantCulture, out var v) ? v : double.NaN,
            }).ToList();
            c.Nivel = nivel.SelectedKey ?? c.Nivel;
            c.Algoritmo = algoritmo.SelectedKey ?? c.Algoritmo;
            c.Populacao = (int)populacao.Value;
            c.Geracoes = (int)geracoes.Value;
            host.ConfigMudou();
            Avisos();
        }

        void Avisos()
        {
            var c = host.Config;
            efetivo.Text = c.Algoritmo == "auto"
                ? "auto → " + c.AlgoritmoEfetivo() + " (" + c.Objetivos.Count + " objetivo" + (c.Objetivos.Count == 1 ? ")" : "s)")
                : "";
            var l = new List<string>();
            if (host.ProblemaDoPainel.Count > 0)
                l.Add("O painel 'problema' está ligado no componente e manda no problema (desligue-o para usar a janela).");
            var sobra = host.OpcoesDoPainel.Intersect(Configuracao.ChavesOpcoes).ToList();
            if (sobra.Count > 0)
                l.Add("O painel 'opcoes' sobrescreve: " + string.Join(", ", sobra) + ".");
            if (host.ProblemaDoPainel.Count == 0) l.AddRange(c.Validar());
            avisos.Text = string.Join("\n", l);
        }

        void Iniciar()
        {
            SalvarConfig();
            var erros = host.ProblemaDoPainel.Count > 0 ? new List<string>() : host.Config.Validar();
            if (erros.Count > 0)
            {
                MessageBox.Show(this, string.Join("\n", erros), "Otimização", MessageBoxType.Warning);
                return;
            }
            ultimoEnviado = "";
            host.Iniciar();
            status.Text = "Iniciando…";
        }

        // ------------------------------------------------------------ servidor → gráficos
        void Consultar()
        {
            botaoIniciar.Enabled = !host.Rodando;
            botaoParar.Enabled = host.Rodando;
            if (!vivo && DateTime.Now < proximaChecagem) return;
            JsonObject pg;
            try
            {
                pg = cli.Pedir("GET", "/progresso", null, vivo ? 5000 : 400);
            }
            catch (Exception)
            {
                vivo = false;
                proximaChecagem = DateTime.Now.AddSeconds(3);    // não trava a interface
                botaoServidor.Visible = true;
                status.Text = "Servidor desligado. 'Ligar servidor' ou 'Iniciar' o sobem.";
                if (ultimoEnviado == "") MostrarArquivo();
                return;
            }
            if (!vivo)
            {
                vivo = true;
                botaoServidor.Visible = false;
                try
                {
                    catalogo = Catalogo.De(cli.Metricas());
                    carregando = true;
                    try { MontarMetricas(); } finally { carregando = false; }
                    if (host.Config.Objetivos.Count == 0 && host.ProblemaDoPainel.Count > 0)
                    {   // começa com o que estava no painel
                        var c = Configuracao.DeProblema(host.ProblemaDoPainel, catalogo);
                        host.Config.Objetivos = c.Objetivos;
                        host.Config.Restricoes = c.Restricoes;
                        CarregarConfig();
                    }
                }
                catch (Exception ex) { status.Text = "Erro lendo as métricas: " + ex.Message; }
            }
            Avisos();
            string estado = J.Texto(pg["estado"]) ?? "";
            if (estado == "livre")
            {
                status.Text = "Servidor ligado, sem otimização em andamento.";
                if (ultimoEnviado == "" || ultimoEnviado.StartsWith("arquivo")) MostrarArquivo();
                return;
            }
            status.Text = "Servidor: " + estado + (pg["erro"] is JsonNode er && er.ToString() != "" ? " — ver o log do componente" : "");
            Enviar(pg, "progresso");
        }

        void MostrarArquivo()
        {
            var arq = host.ArquivoResultado;
            if (arq == null || !File.Exists(arq)) return;
            try { Enviar(DadosGraficos.DeResultadoJson(arq), "arquivo"); }
            catch (Exception ex) { status.Text = "Erro lendo " + arq + ": " + ex.Message; }
        }

        void Enviar(JsonObject dados, string origem)
        {
            dados["rodando"] = host.Rodando;
            var uni = new JsonObject();
            foreach (var m in catalogo) uni[m.Chave] = m.Unidade;
            dados["unidades"] = uni;
            var txt = dados.ToJsonString(ClienteProtocolo.Json);
            if (origem + txt == ultimoEnviado) return;
            if (!paginaPronta) return;                      // a página pede de novo quando carregar
            ultimoEnviado = origem + txt;
            web.ExecuteScriptAsync("otimiza.atualizar(" + txt + ")");
        }

        // ------------------------------------------------------------ gráficos → Rhino
        void Mensagem(string texto)
        {
            JsonObject? m;
            try { m = JsonNode.Parse(texto) as JsonObject; }
            catch (Exception) { return; }
            switch (J.Texto(m?["acao"]))
            {
                case "pronto":
                    paginaPronta = true;
                    ultimoEnviado = "";
                    Consultar();
                    break;
                case "aplicar":
                    if (host.Rodando)
                    {
                        status.Text = "Espere a otimização terminar para aplicar.";
                        return;
                    }
                    if (m?["parametros"] is JsonObject p)
                    {
                        host.Aplicar((JsonObject)p.DeepClone());
                        status.Text = "Solução " + J.Int(m["indice"]) + " aplicada nos sliders.";
                    }
                    break;
            }
        }
    }
}

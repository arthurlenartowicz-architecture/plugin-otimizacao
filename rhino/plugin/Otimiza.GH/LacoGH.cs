// O Ciclo do protocolo num timer da interface (fora do SolveInstance), como o Laco do
// otimizar_gh.py: mexe nos sliders → NewSolution → o componente captura a geometria.
using System;
using System.Collections.Generic;
using System.Linq;
using System.Text.Json.Nodes;
using Grasshopper.Kernel;
using Otimiza.Cliente;
using Rhino;

namespace Otimiza.GH
{
    sealed class LacoGH
    {
        public readonly Ciclo Ciclo;
        public readonly Opcoes Op;
        readonly OtimizarComponent comp;
        readonly GH_Document? doc;
        readonly List<Gene> genes;
        readonly Eto.Forms.UITimer timer;
        readonly bool mostrar;
        bool ocupado, medidor, vistaDesligada;
        string ultimaMensagem = "";

        /// <summary>true enquanto o próprio laço recalcula: o SolveInstance só captura a geometria.</summary>
        public bool Capturando;
        public Gerado? Captura;

        public LacoGH(OtimizarComponent comp, Opcoes op, JsonObject? pedido, List<Gene> genes, Action? subirServidor)
        {
            this.comp = comp;
            Op = op;
            this.genes = genes;
            doc = comp.OnPingDocument();
            mostrar = op.Sim("mostrar", false);
            var cli = new ClienteProtocolo(op.Int("porta", 8765));
            Ciclo = new Ciclo(cli, pedido, genes.Select(g => g.Nome), Gerar, subirServidor) { Lote = op.Int("lote", 4) };
            foreach (var linha in Ciclo.Log) RhinoApp.WriteLine("[Otimizar] " + linha);
            Ciclo.AoEscrever += Escreveu;
            Ciclo.AoTerminar += Terminou;
            Ciclo.AoEsperar += Esperando;
            timer = new Eto.Forms.UITimer { Interval = 0.005 };
            timer.Elapsed += Tick;
            timer.Start();
        }

        void Escreveu(string txt)
        {
            RhinoApp.WriteLine("[Otimizar] " + txt);
            if (!Capturando && doc != null)    // atualiza o painel de log
                doc.ScheduleSolution(50, d => comp.ExpireSolution(false));
        }

        void Vista(bool ligada)
        {
            vistaDesligada = !ligada;
            if (!mostrar && RhinoDoc.ActiveDoc != null) RhinoDoc.ActiveDoc.Views.RedrawEnabled = ligada;
        }

        void Mensagem(string txt)
        {
            if (txt == ultimaMensagem) return;
            ultimaMensagem = txt;
            comp.Message = txt;
            comp.OnDisplayExpired(true);
        }

        void Medidor()
        {
            int pos = (int)(100 * Ciclo.Fracao());
            if (!medidor)
            {
                Rhino.UI.StatusBar.ShowProgressMeter(0, 100,
                    Ciclo.Trabalhador ? "Trabalhando (parar)" : "Otimizando (Esc para parar)", true, true);
                medidor = true;
            }
            Rhino.UI.StatusBar.UpdateProgressMeter(pos, true);
        }

        void EsconderMedidor()
        {
            if (!medidor) return;
            Rhino.UI.StatusBar.HideProgressMeter();
            medidor = false;
        }

        void Tick(object? sender, EventArgs e)
        {
            if (ocupado || doc == null) return;
            if (doc.SolutionState == GH_ProcessStep.Process) return;
            ocupado = true;
            try
            {
                if (GH_Document.IsEscapeKeyDown())
                {
                    Ciclo.Parar("Esc");
                    return;
                }
                Ciclo.Passo(0.25);
                if (Ciclo.Estado == "rodando")
                {
                    if (!vistaDesligada) Vista(false);
                    Medidor();
                }
                Mensagem(Ciclo.Progresso());
            }
            catch (Exception ex)
            {
                RhinoApp.WriteLine("[Otimizar] ERRO no timer: " + ex);
            }
            finally
            {
                ocupado = false;
            }
        }

        Gerado Gerar(Tarefa t)
        {
            Captura = null;
            Capturando = true;
            try
            {
                Genoma.Aplicar(genes, t.Parametros);
                doc!.NewSolution(false);
            }
            finally
            {
                Capturando = false;
            }
            return Captura ?? new Gerado(null, "componente não recalculou");
        }

        public void Parar(string motivo) => Ciclo.Parar(motivo);

        void Esperando()
        {
            Vista(true);
            EsconderMedidor();
            Mensagem(Ciclo.Progresso());
        }

        void Terminou(string estado)
        {
            timer.Stop();
            Vista(true);
            EsconderMedidor();
            Mensagem(estado);
            if (doc == null) return;
            doc.ScheduleSolution(10, d =>
            {
                Genoma.Restaurar(genes);
                foreach (var o in d.Objects)          // recarrega o componente Pareto, se houver
                    if (o.NickName == "Pareto" && o is IGH_ActiveObject a) a.ExpireSolution(false);
                comp.ExpireSolution(false);
            });
        }
    }
}

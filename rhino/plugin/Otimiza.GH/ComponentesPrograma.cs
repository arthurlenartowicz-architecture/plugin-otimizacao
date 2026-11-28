// Componentes do módulo programa (4e): Lote, Programa e Ambiente.
// Lote e Programa escrevem o mesmo JSON do programa.json (ligue a saída na entrada `programa`
// do Otimizar); Ambiente dá nome a um piso, que segue pela entrada `piso` do Otimizar.
using System;
using System.Collections.Generic;
using System.Linq;
using System.Text.Json.Nodes;
using Grasshopper.Kernel;
using Grasshopper.Kernel.Types;
using Otimiza.Cliente;
using Rhino;
using Rhino.Geometry;

namespace Otimiza.GH
{
    /// <summary>Piso com o nome do seu uso (sala, quarto…). Geometria intacta (cópia, sem mexer no original).</summary>
    public sealed class Ambiente
    {
        public string Nome { get; }
        public GeometryBase Geometria { get; }
        public Ambiente(string nome, GeometryBase geometria) { Nome = nome; Geometria = geometria; }
        public override string ToString() => "Ambiente " + Nome;
    }

    public class AmbienteGoo : GH_Goo<Ambiente>, IGH_PreviewData
    {
        public AmbienteGoo() { }
        public AmbienteGoo(Ambiente a) : base(a) { }
        public override bool IsValid => Value != null && Value.Geometria.IsValid;
        public override string TypeName => "Ambiente";
        public override string TypeDescription => "piso com nome de uso (módulo programa)";
        public override IGH_Goo Duplicate() => new AmbienteGoo(Value);
        public override string ToString() => Value?.ToString() ?? "Ambiente vazio";
        public override object ScriptVariable() => Value;   // o Otimizar (C# e GhPython) recebe o Ambiente

        public BoundingBox ClippingBox => Value?.Geometria.GetBoundingBox(false) ?? BoundingBox.Empty;

        public void DrawViewportWires(GH_PreviewWireArgs args)
        {
            if (Value == null) return;
            switch (Value.Geometria)
            {
                case Brep b: args.Pipeline.DrawBrepWires(b, args.Color, 0); break;
                case Mesh m: args.Pipeline.DrawMeshWires(m, args.Color); break;
            }
            args.Pipeline.DrawDot(ClippingBox.Center, Value.Nome);
        }

        public void DrawViewportMeshes(GH_PreviewMeshArgs args)
        {
            if (Value == null) return;
            switch (Value.Geometria)
            {
                case Brep b: args.Pipeline.DrawBrepShaded(b, args.Material); break;
                case Mesh m: args.Pipeline.DrawMeshShaded(m, args.Material); break;
            }
        }
    }

    static class Unidades
    {
        public static double ParaMetro() =>
            RhinoMath.UnitScale(RhinoDoc.ActiveDoc?.ModelUnitSystem ?? UnitSystem.Meters, UnitSystem.Meters);
    }

    public class LoteComponent : GH_Component
    {
        public LoteComponent()
            : base("Lote", "Lote",
                   "Contorno do terreno para o módulo programa (CA, taxa de ocupação, recuos, altura).\n" +
                   "Ligue uma curva fechada e plana: do Rhino (botão direito → Set one Curve) ou do Grasshopper. " +
                   "A curva não é alterada. Ligue a saída em 'programa' do Otimizar ou em 'lote' do Programa.",
                   "Otimiza", "Programa")
        { }

        public override Guid ComponentGuid => new Guid("8d2e4b71-3c5a-4f19-a6e0-91b7c2d45f38");

        protected override void RegisterInputParams(GH_InputParamManager p)
        {
            p.AddCurveParameter("curva", "curva", "contorno fechado e plano do lote", GH_ParamAccess.item);
        }

        protected override void RegisterOutputParams(GH_OutputParamManager p)
        {
            p.AddTextParameter("lote", "lote", "lote em JSON (pontos em metros)", GH_ParamAccess.item);
            p.AddNumberParameter("area", "area", "área do lote (m²)", GH_ParamAccess.item);
        }

        protected override void SolveInstance(IGH_DataAccess DA)
        {
            Curve? c = null;
            if (!DA.GetData(0, ref c) || c == null) return;
            var doc = RhinoDoc.ActiveDoc;
            double tol = doc?.ModelAbsoluteTolerance ?? 0.001;
            if (!c.IsClosed) throw new ArgumentException("a curva do lote precisa ser fechada");
            if (!c.IsPlanar(Math.Max(tol, 1e-6) * 10)) throw new ArgumentException("a curva do lote precisa ser plana");
            if (!c.TryGetPolyline(out Polyline pl))
            {
                // arcos e splines: polígono com flecha ≤ 1 cm (na unidade do modelo)
                var pc = c.ToPolyline(0.01 / Unidades.ParaMetro(), doc?.ModelAngleToleranceRadians ?? 0.0175, 0, 0);
                if (pc == null || !pc.TryGetPolyline(out pl)) throw new ArgumentException("não consegui converter a curva em polígono");
            }
            double k = Unidades.ParaMetro();
            var pts = pl.Select(q => new[] { q.X * k, q.Y * k, q.Z * k }).ToList();
            var lote = ProgramaJson.Lote(pts);
            DA.SetData(0, lote.ToJsonString());
            DA.SetData(1, ProgramaJson.Area(pts));
        }
    }

    public class ProgramaComponent : GH_Component
    {
        public ProgramaComponent()
            : base("Programa", "Programa",
                   "Programa de necessidades: um uso por linha com a sua área-meta (m²), e os pares que devem " +
                   "dividir parede (sala-cozinha ou sala-cozinha:2 com peso). Os nomes são os do componente Ambiente.\n" +
                   "Ligue a saída em 'programa' do Otimizar. Métricas: programa.atendimento_programa, " +
                   "desvio_programa, eficiencia_planta, adjacencia, distancia_ponderada.",
                   "Otimiza", "Programa")
        { }

        public override Guid ComponentGuid => new Guid("4f7a9c20-6e1b-4d83-b5c2-0a3e8f61d7b9");

        protected override void RegisterInputParams(GH_InputParamManager p)
        {
            p[p.AddTextParameter("lote", "lote", "saída do componente Lote (opcional)", GH_ParamAccess.item)].Optional = true;
            p[p.AddTextParameter("usos", "usos", "nome de cada uso (sala, cozinha…)", GH_ParamAccess.list)].Optional = true;
            p[p.AddNumberParameter("areas", "areas", "área-meta de cada uso (m²), na ordem dos usos", GH_ParamAccess.list)].Optional = true;
            p[p.AddTextParameter("adjacencias", "adjacencias", "sala-cozinha, sala-cozinha:2 ou sala, cozinha, 2",
                                 GH_ParamAccess.list)].Optional = true;
            p[p.AddNumberParameter("parede_max", "parede_max", "espessura máxima da parede entre ambientes adjacentes (m; padrão 0,4)",
                                   GH_ParamAccess.item)].Optional = true;
            p[p.AddNumberParameter("comprimento_min", "comprimento_min", "parede comum mínima para contar como adjacente (m; padrão 0,8)",
                                   GH_ParamAccess.item)].Optional = true;
        }

        protected override void RegisterOutputParams(GH_OutputParamManager p)
        {
            p.AddTextParameter("programa", "programa", "programa em JSON (ligue em 'programa' do Otimizar)", GH_ParamAccess.item);
            p.AddTextParameter("resumo", "resumo", "o que vai ao servidor", GH_ParamAccess.item);
        }

        protected override void SolveInstance(IGH_DataAccess DA)
        {
            string? lote = null;
            DA.GetData(0, ref lote);
            var usos = new List<string>(); DA.GetDataList(1, usos);
            var areas = new List<double>(); DA.GetDataList(2, areas);
            var adj = new List<string>(); DA.GetDataList(3, adj);
            var baseObj = string.IsNullOrWhiteSpace(lote) ? null : ProgramaJson.Juntar(new[] { lote! });
            var prog = ProgramaJson.Montar(baseObj, usos, areas, adj);
            double pm = double.NaN, cm = double.NaN;
            var lim = new JsonObject();
            if (DA.GetData(4, ref pm)) lim["parede_max"] = pm;
            if (DA.GetData(5, ref cm)) lim["comprimento_min"] = cm;
            if (lim.Count > 0) prog["adjacencia"] = lim;
            DA.SetData(0, prog.ToJsonString());
            DA.SetData(1, ProgramaJson.Resumo(prog));
        }
    }

    public class AmbienteComponent : GH_Component
    {
        public AmbienteComponent()
            : base("Ambiente", "Ambiente",
                   "Dá a um piso o nome do seu uso (o mesmo do componente Programa). Ligue a saída na entrada " +
                   "'piso' do Otimizar, junto com os outros pisos. Vários pisos com o mesmo nome somam a área.",
                   "Otimiza", "Programa")
        { }

        public override Guid ComponentGuid => new Guid("b6c13e58-90d4-4a27-8f6b-2e5d7a0c9143");

        protected override void RegisterInputParams(GH_InputParamManager p)
        {
            p.AddGenericParameter("piso", "piso", "superfície do piso (Brep, Surface, Mesh, Extrusion)", GH_ParamAccess.item);
            p.AddTextParameter("nome", "nome", "uso do ambiente (sala, quarto…)", GH_ParamAccess.item);
        }

        protected override void RegisterOutputParams(GH_OutputParamManager p)
        {
            p.AddGenericParameter("ambiente", "ambiente", "piso com nome (ligue em 'piso' do Otimizar)", GH_ParamAccess.item);
        }

        protected override void SolveInstance(IGH_DataAccess DA)
        {
            IGH_Goo? g = null;
            string? nome = null;
            if (!DA.GetData(0, ref g) || g == null) return;
            if (!DA.GetData(1, ref nome) || string.IsNullOrWhiteSpace(nome)) throw new ArgumentException("ambiente sem nome");
            GeometryBase geo = g.ScriptVariable() switch
            {
                Brep b => b.DuplicateBrep(),
                Mesh m => m.DuplicateMesh(),
                Extrusion e => e.ToBrep(),
                BrepFace f => f.DuplicateFace(false),
                Surface s => s.ToBrep(),
                Ambiente a => a.Geometria,
                object o => throw new ArgumentException("piso não é superfície/malha: " + o.GetType().Name),
                null => throw new ArgumentException("piso vazio"),
            };
            DA.SetData(0, new AmbienteGoo(new Ambiente(nome!.Trim(), geo)));
        }
    }
}

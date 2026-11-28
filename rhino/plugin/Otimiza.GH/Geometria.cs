// Geometria do Grasshopper → protocolo v1 (mesmas regras do otimizar_gh.py).
using System;
using System.Collections.Generic;
using System.Linq;
using System.Text.Json.Nodes;
using Otimiza.Cliente;
using Rhino.Geometry;

namespace Otimiza.GH
{
    static class Geometria
    {
        public static readonly string[] Projeto = { "fachada", "vidro", "cobertura", "piso", "interno" };
        public static readonly string[] Fixos = { "contexto", "entorno" };

        public static MeshingParameters Parametros(string qual)
        {
            switch (qual)
            {
                case "rapida": return MeshingParameters.FastRenderMesh;
                case "padrao": return MeshingParameters.Default;
                case "qualidade": return MeshingParameters.QualityRenderMesh;
                default:
                    var mp = MeshingParameters.Minimal;
                    mp.SimplePlanes = true;      // face plana = poucos triângulos (não uma grade)
                    return mp;
            }
        }

        static void Malhar(Brep b, MeshingParameters mp, List<Mesh> partes)
        {
            var ms = Mesh.CreateFromBrep(b, mp);
            if (ms != null) partes.AddRange(ms);
        }

        /// <summary>Todas as geometrias de um rótulo numa malha só (base64), ou null se vazio.</summary>
        public static JsonObject? Malha(IEnumerable<object?> objetos, MeshingParameters mp)
        {
            var partes = new List<Mesh>();
            foreach (var o in objetos) Adicionar(o);
            void Adicionar(object? o)
            {
                switch (o)
                {
                    case null: break;
                    case Ambiente a: Adicionar(a.Geometria); break;
                    case Mesh m: partes.Add(m); break;
                    case Brep b: Malhar(b, mp, partes); break;
                    case Extrusion e: Malhar(e.ToBrep(), mp, partes); break;
                    case BrepFace f: Malhar(f.DuplicateFace(false), mp, partes); break;   // (é Surface)
                    case Surface s: Malhar(s.ToBrep(), mp, partes); break;
                    default: throw new ArgumentException("geometria não suportada: " + o.GetType().Name);
                }
            }
            if (partes.Count == 0) return null;
            var malha = new Mesh();
            malha.Append(partes);
            if (malha.Faces.Count == 0) return null;
            malha.Faces.ConvertQuadsToTriangles();
            return Codificacao.Malha(malha.Vertices.ToFloatArray(), malha.Faces.ToIntArray(true));
        }

        public static JsonObject Malhas(IDictionary<string, List<object?>> entradas, IEnumerable<string> rotulos,
                                        MeshingParameters mp)
        {
            var saida = new JsonObject();
            foreach (var r in rotulos)
            {
                if (!entradas.TryGetValue(r, out var objs)) continue;
                if (r == "piso" && objs.Any(o => o is Ambiente))
                {
                    // pisos com nome (componente Ambiente): um item por ambiente, o resto junto
                    var itens = new JsonArray();
                    foreach (var a in objs.OfType<Ambiente>())
                    {
                        var ma = Malha(new object?[] { a.Geometria }, mp);
                        if (ma == null) continue;
                        ma["ambiente"] = a.Nome;
                        itens.Add(ma);
                    }
                    var resto = Malha(objs.Where(o => !(o is Ambiente)), mp);
                    if (resto != null) itens.Add(resto);
                    if (itens.Count > 0) saida[r] = itens;
                    continue;
                }
                var m = Malha(objs, mp);
                if (m != null) saida[r] = m;
            }
            return saida;
        }

        static IEnumerable<Line> Segmentos(object o)
        {
            switch (o)
            {
                case Line l: return new[] { l };
                case Polyline pl: return pl.GetSegments();
                case Curve c:
                    return c.TryGetPolyline(out Polyline p) ? p.GetSegments()
                                                             : new[] { new Line(c.PointAtStart, c.PointAtEnd) };
                default: throw new ArgumentException("barra não é linha/curva: " + o.GetType().Name);
            }
        }

        static JsonArray Pontos(IEnumerable<object?> objetos)
        {
            var a = new JsonArray();
            foreach (var o in objetos)
            {
                Point3d p;
                switch (o)
                {
                    case null: continue;
                    case Point3d q: p = q; break;
                    case Point q: p = q.Location; break;
                    default: throw new ArgumentException("apoio/carga não é ponto: " + o.GetType().Name);
                }
                a.Add(p.X); a.Add(p.Y); a.Add(p.Z);
            }
            return a;
        }

        public static JsonObject? Estrutura(IDictionary<string, List<object?>> e, Opcoes op)
        {
            var barras = new JsonArray();
            foreach (var o in e["barras"])
            {
                if (o == null) continue;
                foreach (var s in Segmentos(o))
                {
                    barras.Add(s.From.X); barras.Add(s.From.Y); barras.Add(s.From.Z);
                    barras.Add(s.To.X); barras.Add(s.To.Y); barras.Add(s.To.Z);
                }
            }
            if (barras.Count == 0) return null;
            var d = new JsonObject
            {
                ["barras"] = barras, ["trelica"] = op.Sim("trelica", false), ["gravidade"] = op.Num("gravidade", 9.81),
            };
            var sec = e["secao"].Where(x => x != null).Select(x => x!.ToString() ?? "").ToList();
            if (sec.Count == 0) sec.Add(op.Texto("secao", "RO114.3/4"));
            d["secao"] = sec.Count == 1 ? (JsonNode)sec[0] : new JsonArray(sec.Select(s => (JsonNode)s).ToArray());
            var ap = Pontos(e["apoios"]);
            if (ap.Count > 0)
                d["apoios"] = new JsonArray(new JsonObject { ["pontos"] = ap, ["condicoes"] = op.Texto("apoio", "articulado") });
            var cg = Pontos(e["cargas"]);
            if (cg.Count > 0)
            {
                var f = new JsonArray(op.Vetor("forca", "0,0,-10").Select(x => (JsonNode)x).ToArray());
                d["cargas"] = new JsonArray(new JsonObject { ["pontos"] = cg, ["forca"] = f });
            }
            return d;
        }

        /// <summary>A variante que está no canvas agora, no formato do protocolo.</summary>
        public static JsonObject Atual(IDictionary<string, List<object?>> entradas, Opcoes op)
        {
            var e = Estrutura(entradas, op);
            if (e != null) return new JsonObject { ["estrutura"] = e };
            var m = Malhas(entradas, Projeto, Parametros(op.Texto("malha", "minima")));
            if (m.Count == 0)
                throw new ArgumentException("nenhuma geometria nas entradas fachada/vidro/cobertura/piso/interno/barras");
            return new JsonObject { ["malhas"] = m };
        }
    }
}

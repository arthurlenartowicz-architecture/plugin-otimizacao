// Arrays do protocolo em base64: float32 (vértices) e int32 (faces), little-endian,
// igual ao otimizar_gh.py (ToFloatArray / ToIntArray(true) + BlockCopy + ToBase64String).
using System;
using System.Text.Json.Nodes;

namespace Otimiza.Cliente
{
    public static class Codificacao
    {
        static void ConferirOrdem()
        {
            if (!BitConverter.IsLittleEndian)
                throw new PlatformNotSupportedException("o protocolo usa little-endian");
        }

        public static string Base64(float[] a)
        {
            ConferirOrdem();
            var b = new byte[a.Length * 4];
            Buffer.BlockCopy(a, 0, b, 0, b.Length);
            return Convert.ToBase64String(b);
        }

        public static string Base64(int[] a)
        {
            ConferirOrdem();
            var b = new byte[a.Length * 4];
            Buffer.BlockCopy(a, 0, b, 0, b.Length);
            return Convert.ToBase64String(b);
        }

        /// <summary>Malha do protocolo: vertices = x y z seguidos, faces = i j k (triângulos).</summary>
        public static JsonObject Malha(float[] vertices, int[] faces)
        {
            if (vertices.Length % 3 != 0 || faces.Length % 3 != 0)
                throw new ArgumentException("vértices e faces precisam ter múltiplos de 3 valores");
            return new JsonObject { ["vertices"] = Base64(vertices), ["faces"] = Base64(faces) };
        }
    }
}

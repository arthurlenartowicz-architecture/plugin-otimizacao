// Cliente HTTP do protocolo v1 (ver rhino/grasshopper/LEIAME.md, "Protocolo (versão 1)").
using System;
using System.IO;
using System.Net.Http;
using System.Text;
using System.Text.Encodings.Web;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Threading;

namespace Otimiza.Cliente
{
    public sealed class ErroServidor : Exception
    {
        public ErroServidor(string msg) : base(msg) { }
    }

    /// <summary>Pedidos JSON ao servidor (127.0.0.1). Chamadas síncronas: cada uma leva ~1 ms
    /// em localhost, e o laço roda no timer da interface, como no otimizar_gh.py.</summary>
    public sealed class ClienteProtocolo : IDisposable
    {
        public const int Versao = 1;

        // sem escapar '+' do base64 (o padrão do .NET vira "+": +8% de texto)
        public static readonly JsonSerializerOptions Json = new JsonSerializerOptions
        {
            Encoder = JavaScriptEncoder.UnsafeRelaxedJsonEscaping,
        };

        readonly HttpClient http;
        public string Base { get; }

        public ClienteProtocolo(int porta = 8765) : this("http://127.0.0.1:" + porta) { }

        public ClienteProtocolo(string url)
        {
            Base = url.TrimEnd('/');
            var h = new SocketsHttpHandler { UseProxy = false, PooledConnectionIdleTimeout = TimeSpan.FromSeconds(30) };
            http = new HttpClient(h) { Timeout = Timeout.InfiniteTimeSpan };
        }

        public JsonObject Pedir(string metodo, string rota, JsonNode? dados = null, int timeoutMs = 600000)
        {
            using var req = new HttpRequestMessage(new HttpMethod(metodo), Base + rota);
            if (dados != null)
                req.Content = new StringContent(dados.ToJsonString(Json), Encoding.UTF8, "application/json");
            else if (metodo == "POST")
                req.Content = new ByteArrayContent(Array.Empty<byte>());
            using var cts = new CancellationTokenSource(timeoutMs);
            HttpResponseMessage resp;
            try
            {
                resp = http.Send(req, cts.Token);
            }
            catch (Exception e) when (e is HttpRequestException || e is OperationCanceledException)
            {
                throw new ErroServidor("servidor fora do ar (" + e.Message + ")");
            }
            using (resp)
            {
                string txt;
                using (var leitor = new StreamReader(resp.Content.ReadAsStream(cts.Token), Encoding.UTF8))
                    txt = leitor.ReadToEnd();
                JsonObject d = new JsonObject();
                if (!string.IsNullOrEmpty(txt))
                {
                    try { d = JsonNode.Parse(txt) as JsonObject ?? d; }
                    catch (JsonException) { if ((int)resp.StatusCode < 400) throw; }
                }
                if ((int)resp.StatusCode >= 400)
                    throw new ErroServidor(J.Texto(d["erro"]) ?? txt);
                return d;
            }
        }

        public bool Vivo()
        {
            try { Pedir("GET", "/estado", null, 1500); return true; }
            catch (Exception) { return false; }
        }

        public JsonObject Estado() => Pedir("GET", "/estado");
        public JsonObject Metricas() => Pedir("GET", "/metricas");
        public JsonObject Iniciar(JsonObject pedido) => Pedir("POST", "/iniciar", pedido);
        public JsonObject Tarefas(int maximo) => Pedir("POST", "/tarefas", new JsonObject { ["maximo"] = maximo });
        public JsonObject Resultados(JsonArray lista) => Pedir("POST", "/resultados", new JsonObject { ["resultados"] = lista });
        public JsonObject Parar() => Pedir("POST", "/parar", new JsonObject());
        public JsonObject Devolver(JsonArray ids) => Pedir("POST", "/devolver", new JsonObject { ["ids"] = ids });
        public JsonObject Avaliar(JsonObject pedido) => Pedir("POST", "/avaliar", pedido);
        public JsonObject Encerrar() => Pedir("POST", "/encerrar");

        public void Dispose() => http.Dispose();
    }

    /// <summary>Leitura tolerante de JsonNode (número pode vir como texto e vice-versa).</summary>
    public static class J
    {
        public static string? Texto(JsonNode? n)
        {
            if (n == null) return null;
            if (n is JsonValue v && v.TryGetValue(out string? s)) return s;
            return n.ToJsonString();
        }

        public static double Num(JsonNode? n, double padrao = double.NaN)
        {
            if (n is JsonValue v)
            {
                if (v.TryGetValue(out double d)) return d;
                if (v.TryGetValue(out string? s) && double.TryParse(s, System.Globalization.NumberStyles.Float,
                        System.Globalization.CultureInfo.InvariantCulture, out d)) return d;
                if (v.TryGetValue(out bool b)) return b ? 1 : 0;
            }
            return padrao;
        }

        public static int Int(JsonNode? n, int padrao = 0)
        {
            double d = Num(n);
            return double.IsNaN(d) ? padrao : (int)Math.Round(d);
        }
    }
}

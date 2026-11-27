"""HTTP + JSON em localhost (só a biblioteca padrão). Rotas em rhino/grasshopper/LEIAME.md.

    .venv\\Scripts\\python.exe -m otimiza.servidor [--porta 8765]
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from otimiza.servidor import protocolo as pr
from otimiza.servidor.servico import Servico, metricas

PORTA = 8765


def _rotas(servico: Servico, desligar):
    def estado(_):
        if servico.otim is None:
            return dict(versao=pr.VERSAO, estado="livre")
        return servico.otim.estado_dict()

    return {
        ("GET", "/estado"): estado,
        ("GET", "/metricas"): lambda _: dict(versao=pr.VERSAO, metricas=metricas()),
        ("POST", "/iniciar"): servico.iniciar,
        ("POST", "/tarefas"): lambda d: servico.exigir().tarefas(int(d.get("maximo", 0))),
        ("POST", "/resultados"): lambda d: servico.exigir().resultados(d.get("resultados", [])),
        ("POST", "/parar"): lambda _: servico.exigir().parar(),
        ("POST", "/devolver"): lambda d: servico.exigir().devolver(d.get("ids", [])),
        ("POST", "/avaliar"): servico.avaliar,
        ("POST", "/encerrar"): lambda _: (desligar(), dict(estado="encerrando"))[1],
    }


def criar(porta: int = PORTA, servico: Servico | None = None) -> ThreadingHTTPServer:
    """Servidor pronto (sem rodar). `servidor.serve_forever()` para atender."""
    servico = servico or Servico()
    srv: ThreadingHTTPServer

    def desligar():
        threading.Thread(target=lambda: (servico.encerrar(), srv.shutdown()), daemon=True).start()

    rotas = _rotas(servico, desligar)

    class Tratador(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"      # conexão mantida aberta (menos latência por pedido)

        def log_message(self, *a):         # sem log por pedido no console
            pass

        def _responder(self, codigo: int, dados: dict):
            corpo = json.dumps(dados, ensure_ascii=False, allow_nan=False,
                               default=_json_padrao).encode("utf-8")
            self.send_response(codigo)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(corpo)))
            self.end_headers()
            self.wfile.write(corpo)

        def _tratar(self, metodo: str):
            url = urlparse(self.path)
            fn = rotas.get((metodo, url.path.rstrip("/") or "/"))
            if fn is None:
                return self._responder(404, dict(erro=f"rota desconhecida: {metodo} {url.path}"))
            try:
                n = int(self.headers.get("Content-Length") or 0)
                corpo = self.rfile.read(n) if n else b""
                dados = json.loads(corpo.decode("utf-8")) if corpo else {}
                dados.update({k: v[-1] for k, v in parse_qs(url.query).items()})
                self._responder(200, _limpar(fn(dados)))
            except (pr.ErroProtocolo, KeyError, TypeError, json.JSONDecodeError) as e:
                self._responder(400, dict(erro=f"{type(e).__name__}: {e}"))
            except Exception:
                self._responder(500, dict(erro=traceback.format_exc()[-4000:]))

        def do_GET(self):
            self._tratar("GET")

        def do_POST(self):
            self._tratar("POST")

    srv = ThreadingHTTPServer(("127.0.0.1", porta), Tratador)
    srv.daemon_threads = True
    srv.servico = servico
    return srv


def _json_padrao(o):
    import numpy as np
    if isinstance(o, np.generic):
        return o.item()
    if isinstance(o, np.ndarray):
        return o.tolist()
    return str(o)


def _limpar(o):
    """NaN/inf → None (JSON válido para o .NET)."""
    import math
    if isinstance(o, float):
        return o if math.isfinite(o) else None
    if isinstance(o, dict):
        return {k: _limpar(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_limpar(v) for v in o]
    return o


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="servidor do motor de otimização (localhost)")
    ap.add_argument("--porta", type=int, default=PORTA)
    a = ap.parse_args(argv)
    print(f"carregando módulos e GPU…", flush=True)
    srv = criar(a.porta)
    print(f"servidor em http://127.0.0.1:{a.porta}  (Ctrl+C ou POST /encerrar para sair)", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.servico.encerrar()
        srv.server_close()


if __name__ == "__main__":
    main()

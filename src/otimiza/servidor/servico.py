"""Estado do servidor: otimização em andamento, fila de tarefas e avaliação na GPU.

Fluxo (uma geração):
  1. A `Sessao` (ask) dá os parâmetros do lote. Projetos já avaliados antes saem do
     cache e não voltam ao cliente; repetidos dentro do lote viram uma tarefa só.
  2. Os clientes (um ou vários Rhinos) pegam tarefas (`tarefas`), geram a geometria
     e devolvem (`resultados`). Tarefa sem resposta depois de `reatribuir_s` volta à
     fila (cliente que travou não para a otimização).
  3. Com o lote completo, a avaliação vai para a thread da GPU (uma só, em ordem);
     o HTTP continua respondendo ("aguardar") e o Rhino não trava.
  4. `Sessao.responder` (tell) → próximo lote … No fim (ou em `parar`), grava o
     resultado.json pelo `pareto.exportar`, com a geometria recebida.
Sem HTTP aqui (testável direto); a rede fica em `rede.py`.
"""
from __future__ import annotations

import threading
import time
import traceback
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path

import numpy as np

from otimiza.servidor import protocolo as pr

RAIZ = Path(__file__).resolve().parents[3]


def chave(p: dict) -> tuple:
    return tuple(sorted((k, str(v)) for k, v in p.items()))


def carregar_modulos():
    """Registra os módulos (import com efeito de registro)."""
    import otimiza.modulos.estrutura  # noqa: F401
    import otimiza.modulos.sol_luz  # noqa: F401
    import otimiza.modulos.termico  # noqa: F401


def metricas() -> dict:
    from otimiza.nucleo.contrato import registrados
    carregar_modulos()
    return {n: {k: dict(unidade=m.unidade, sentido=m.sentido, descricao=m.descricao)
                for k, m in mod.metricas.items()} for n, mod in sorted(registrados().items())}


class Servico:
    """Uma otimização por vez + avaliações avulsas; uma thread para a GPU."""

    def __init__(self):
        carregar_modulos()
        self.gpu = ThreadPoolExecutor(max_workers=1, thread_name_prefix="gpu")
        self.trava = threading.RLock()
        self.climas: dict[str, tuple] = {}   # caminho do EPW → (Clima, Cache)
        self.otim: Otimizacao | None = None
        self.rodadas = 0                     # nº de otimizações iniciadas (vai no id das tarefas)

    # ---------------------------------------------------------------- clima
    def clima(self, epw: str | None):
        """(Clima, Cache) do EPW (lido uma vez; o cache do céu vale entre otimizações)."""
        from otimiza.nucleo.cache import Cache
        from otimiza.nucleo.clima import ler_epw
        if not epw:
            epws = sorted((RAIZ / "data" / "epw").glob("*.epw"))
            if not epws:
                raise pr.ErroProtocolo("informe 'epw' (nenhum .epw em data/epw)")
            epw = str(epws[0])
        epw = str(Path(epw).resolve())
        if epw not in self.climas:
            if not Path(epw).is_file():
                raise pr.ErroProtocolo(f"EPW não encontrado: {epw}")
            self.climas[epw] = (ler_epw(epw), Cache())
        return self.climas[epw]

    # ---------------------------------------------------------------- avaliação
    def avaliar_objetos(self, objetos: list, chaves: list[str], nivel: str, clima, cache):
        """Roda na thread da GPU: {"modulo.metrica": array (n,)} ."""
        from otimiza.nucleo.motor import avaliar
        cena = self._cena(objetos, clima)
        cache.resultados.clear()
        vals = avaliar(cena, chaves, nivel, cache)
        return {k: v.detach().double().cpu().numpy() for k, v in vals.items()}

    @staticmethod
    def _cena(objetos, clima):
        from otimiza.modulos.estrutura.fem import Estrutura
        from otimiza.nucleo.cena import CenaLote
        if all(isinstance(o, Estrutura) for o in objetos):
            return CenaLote(variantes=[], clima=clima, estruturas=objetos)
        if any(isinstance(o, Estrutura) for o in objetos):
            raise pr.ErroProtocolo("lote misturando malhas e estruturas")
        return CenaLote(variantes=objetos, clima=clima)

    def avaliar(self, pedido: dict) -> dict:
        """Avaliação avulsa (sem otimizar): {"geometrias": [...], "metricas": [...]}."""
        cfg = Config(pedido, self)
        objs = [pr.geometria_de(g, cfg.escala, cfg.materiais, cfg.fixas)[0]
                for g in pedido.get("geometrias", [])]
        if not objs:
            raise pr.ErroProtocolo("'geometrias' vazio")
        chaves = pedido.get("metricas") or cfg.problema.metricas()
        t0 = time.perf_counter()
        vals = self.gpu.submit(self.avaliar_objetos, objs, chaves, cfg.nivel, cfg.clima,
                               cfg.cache).result()
        return dict(valores={k: [None if not np.isfinite(x) else float(x) for x in v]
                             for k, v in vals.items()},
                    tempo_s=round(time.perf_counter() - t0, 4))

    # ---------------------------------------------------------------- otimização
    def iniciar(self, pedido: dict) -> dict:
        with self.trava:
            if self.otim and self.otim.estado in ("gerando", "avaliando"):
                if not pedido.get("substituir", True):
                    raise pr.ErroProtocolo("já existe uma otimização em andamento")
                self.otim.parar()
            cfg = Config(pedido, self)
            self.rodadas += 1
            self.otim = Otimizacao(cfg, self, self.rodadas)
            return self.otim.estado_dict()

    def exigir(self) -> "Otimizacao":
        if self.otim is None:
            raise pr.ErroProtocolo("nenhuma otimização iniciada (POST /iniciar)")
        return self.otim

    def encerrar(self):
        if self.otim:
            self.otim.parar()
        self.gpu.shutdown(wait=True)


class Config:
    """Campos comuns de /iniciar e /avaliar."""

    def __init__(self, d: dict, servico: Servico):
        v = d.get("versao", pr.VERSAO)
        if int(v) != pr.VERSAO:
            raise pr.ErroProtocolo(f"versão do protocolo {v} (o servidor fala a {pr.VERSAO})")
        self.dados = d
        self.espaco = pr.espaco_de(d["parametros"]) if d.get("parametros") else None
        self.problema = pr.problema_de(d["problema"]) if d.get("problema") else None
        if self.problema:
            from otimiza.nucleo.motor import separar
            from otimiza.nucleo.contrato import modulo
            for c in self.problema.metricas():
                m, met = separar(c)
                try:
                    mod = modulo(m)
                except KeyError as e:
                    raise pr.ErroProtocolo(str(e)) from None
                if met not in mod.metricas:
                    raise pr.ErroProtocolo(f"{m!r} não tem a métrica {met!r} "
                                           f"(tem: {sorted(mod.metricas)})")
        from otimiza.nucleo.contrato import NIVEIS
        self.nivel = d.get("nivel", "rapido")
        if self.nivel not in NIVEIS:
            raise pr.ErroProtocolo(f"nível deve ser um de {NIVEIS}")
        self.escala = float(d.get("escala", 1.0))
        self.materiais = d.get("materiais")
        pr.materiais_de(self.materiais)          # valida já
        fixas = (d.get("fixas") or {}).get("malhas")
        # decodifica o contexto uma vez só (base64 → arrays)
        self.fixas = ({r: [{"vertices": pr.array(m["vertices"], m.get("tipo_vertices", "f4"), 3),
                            "tipo_vertices": "f8", "faces": pr.array(m["faces"], "i4", 3)}
                           for m in (x if isinstance(x, list) else [x]) if m]
                       for r, x in fixas.items()} if fixas else None)
        chaves = self.problema.metricas() if self.problema else list(d.get("metricas", []))
        precisa_clima = any(not c.startswith("estrutura.") for c in chaves)
        self.clima, self.cache = servico.clima(d.get("epw")) if precisa_clima else (None, None)
        if self.cache is None:
            from otimiza.nucleo.cache import Cache
            self.cache = Cache()


class Otimizacao:
    def __init__(self, cfg: Config, servico: Servico, rodada: int = 1):
        from otimiza.otimizacao.otimizador import Sessao
        d = cfg.dados
        if cfg.espaco is None or cfg.problema is None:
            raise pr.ErroProtocolo("/iniciar precisa de 'parametros' e 'problema'")
        self.cfg, self.servico, self.rodada = cfg, servico, rodada
        self.trava = threading.RLock()
        p = cfg.problema
        self.sessao = Sessao(cfg.espaco, p.objetivos, [p.sentido(o) for o in p.objetivos],
                             d.get("algoritmo", "auto"), int(d.get("populacao", 48)),
                             int(d.get("geracoes", 20)), int(d.get("semente", 0)))
        from otimiza.otimizacao.progresso import Progresso
        self.progresso = Progresso(self.sessao.nomes, self.sessao.sentidos)   # GET /progresso
        self.caso = str(d.get("caso", "gh"))
        self.saida = Path(d.get("saida") or RAIZ / "resultados" / self.caso)
        self.reatribuir_s = float(d.get("reatribuir_s", 120))
        self.memo: dict[tuple, tuple[np.ndarray, float]] = {}   # projeto → (F, violação)
        self.geom: dict[tuple, object] = {}                     # projeto → geometria (exportar)
        self.tempos_cliente: list[float] = []                   # s por variante (lado do GH)
        self.tempos_gpu: list[tuple[int, float]] = []           # (n variantes, s) por lote
        self.erro: str | None = None
        self.arquivo: str | None = None
        self.estado = "gerando"
        self.t0 = time.perf_counter()
        self._futuro: Future | None = None
        self._novo_lote()

    # ---------------------------------------------------------------- lote
    def _novo_lote(self):
        params = self.sessao.pedir()
        if params is None:
            self._terminar()
            return
        self.lote = params
        self.geracao = self.sessao.geracao
        self.chaves = [chave(p) for p in params]
        self.recebidos: dict[int, tuple] = {}     # índice → (objeto, objeto p/ exportar) | None
        primeiro: dict[tuple, int] = {}
        self.pendentes: list[int] = []
        self.atribuidos: dict[int, float] = {}
        for i, k in enumerate(self.chaves):
            if k in self.memo or k in primeiro:
                continue                           # já avaliado / repetido no lote
            primeiro[k] = i
            self.pendentes.append(i)
        self.estado = "gerando"
        if not self.pendentes:
            self._avaliar()

    def tarefas(self, maximo: int = 0) -> dict:
        with self.trava:
            if self.estado != "gerando":
                return dict(self.estado_dict(), tarefas=[])
            if not self.pendentes:                # tudo distribuído: reenvia as atrasadas
                agora = time.perf_counter()
                atrasadas = [i for i, t in self.atribuidos.items()
                             if i not in self.recebidos and agora - t > self.reatribuir_s]
                self.pendentes.extend(sorted(atrasadas))
            n = len(self.pendentes) if maximo <= 0 else min(maximo, len(self.pendentes))
            dar, self.pendentes = self.pendentes[:n], self.pendentes[n:]
            agora = time.perf_counter()
            for i in dar:
                self.atribuidos[i] = agora
            saida = self.estado_dict()
            if not dar:
                saida["estado"] = "aguardar"
                saida["motivo"] = "esperando outros clientes terminarem o lote"
            saida["tarefas"] = [dict(id=f"{self.rodada}-{self.geracao}-{i}", parametros=self.lote[i])
                                for i in dar]
            return saida

    def resultados(self, lista: list[dict]) -> dict:
        """[{"id", "geometria": {...}} | {"id", "erro": "..."}, "tempo_s"?]"""
        aceitos, erros = 0, []
        convertidos = []
        for r in lista:            # decodifica fora da trava (vários clientes ao mesmo tempo)
            rod, g, i = self._id(r.get("id"))
            obj = None
            if r.get("geometria") is not None and not r.get("erro"):
                try:
                    obj = pr.geometria_de(r["geometria"], self.cfg.escala, self.cfg.materiais,
                                          self.cfg.fixas)
                except pr.ErroProtocolo as e:
                    erros.append(f"{r['id']}: {e}")
            elif r.get("erro"):
                erros.append(f"{r['id']}: {r['erro']}")
            convertidos.append((rod, g, i, obj, r.get("tempo_s")))
        with self.trava:
            for rod, g, i, obj, t in convertidos:
                if self.estado != "gerando" or (rod, g) != (self.rodada, self.geracao) \
                        or i in self.recebidos \
                        or not 0 <= i < len(self.lote):
                    continue      # atrasada (geração anterior) ou repetida
                self.recebidos[i] = obj
                aceitos += 1
                if t is not None:
                    self.tempos_cliente.append(float(t))
            if erros:
                self.erro = "; ".join(erros)[-2000:]
            if self.estado == "gerando" and not self.pendentes and \
                    all(i in self.recebidos for i in self.atribuidos):
                self._avaliar()
            saida = self.estado_dict()
            saida.update(aceitos=aceitos, erros=erros)
            return saida

    @staticmethod
    def _id(texto) -> tuple:
        """Id "rodada-geração-índice" → três inteiros."""
        try:
            rod, g, i = (int(x) for x in str(texto).split("-"))
        except (TypeError, ValueError):
            raise pr.ErroProtocolo(f"id de tarefa inválido: {texto!r}") from None
        return rod, g, i

    def devolver(self, ids: list) -> dict:
        """Tarefas pegas e não feitas (cliente parou): voltam já para a fila."""
        n = 0
        with self.trava:
            for texto in ids:
                rod, g, i = self._id(texto)
                if self.estado == "gerando" and (rod, g) == (self.rodada, self.geracao) \
                        and i in self.atribuidos and i not in self.recebidos \
                        and i not in self.pendentes:
                    del self.atribuidos[i]
                    self.pendentes.insert(0, i)
                    n += 1
            return dict(self.estado_dict(), devolvidas=n)

    # ---------------------------------------------------------------- GPU
    def _avaliar(self):
        self.estado = "avaliando"
        self._futuro = self.servico.gpu.submit(self._avaliar_gpu, self.geracao)

    def _avaliar_gpu(self, geracao: int):
        try:
            idx = [i for i, o in self.recebidos.items() if o is not None]
            F_novo, v_novo = None, None
            t0 = time.perf_counter()
            if idx:
                from otimiza.nucleo.motor import avaliar_problema
                cena = self.servico._cena([self.recebidos[i][0] for i in idx], self.cfg.clima)
                self.cfg.cache.resultados.clear()
                F_t, v_t = avaliar_problema(cena, self.cfg.problema, self.cfg.nivel, self.cfg.cache)
                F_novo = F_t.detach().double().cpu().numpy()
                v_novo = v_t.detach().double().cpu().numpy()
                self.tempos_gpu.append((len(idx), time.perf_counter() - t0))
            with self.trava:
                if self.estado != "avaliando" or geracao != self.geracao:
                    return             # parado no meio
                m = len(self.sessao.nomes)
                for j, i in enumerate(idx):
                    self.memo[self.chaves[i]] = (F_novo[j], float(v_novo[j]))
                    self.geom[self.chaves[i]] = self.recebidos[i][1]
                for i, o in self.recebidos.items():
                    if o is None and self.chaves[i] not in self.memo:
                        self.memo[self.chaves[i]] = (np.full(m, np.nan), np.nan)  # erro → inviável
                F = np.array([self.memo[k][0] for k in self.chaves], dtype=float).reshape(-1, m)
                viol = np.array([self.memo[k][1] for k in self.chaves], dtype=float)
                self.sessao.responder(F, viol)
                self.progresso.registrar(*self.sessao.problema.registro[-1])  # mesmos F/violação
                self._novo_lote()
        except Exception:
            with self.trava:
                self.erro = traceback.format_exc()[-4000:]
                self.estado = "erro"
                self._exportar()

    # ---------------------------------------------------------------- fim
    def _terminar(self):
        self.estado = "fim"
        self._exportar()

    def parar(self) -> dict:
        with self.trava:
            if self.estado in ("gerando", "avaliando"):
                self.estado = "fim"
                self._exportar()
            return self.estado_dict()

    def resultado(self):
        r = self.sessao.resultado()
        r.info.update(caso=self.caso, nivel=self.cfg.nivel, problema=self.cfg.dados.get("problema"),
                      cliente=self.cfg.dados.get("cliente"), **self.tempos())
        return r

    def _exportar(self):
        from otimiza.otimizacao.pareto import exportar
        if not self.sessao.problema.registro:
            return
        try:
            r = self.resultado()
            exportar(r, str(self.saida), gerador=lambda p: self.geom[chave(p)])
            self.arquivo = str(self.saida / "resultado.json")
        except Exception:
            self.erro = (self.erro or "") + "\nao exportar:\n" + traceback.format_exc()[-3000:]

    def tempos(self) -> dict:
        tc = self.tempos_cliente
        n_gpu = sum(n for n, _ in self.tempos_gpu)
        s_gpu = sum(t for _, t in self.tempos_gpu)
        return dict(tempo_cliente_s_por_variante=round(float(np.mean(tc)), 4) if tc else None,
                    tempo_gpu_s_por_variante=round(s_gpu / n_gpu, 5) if n_gpu else None,
                    tempo_gpu_s=round(s_gpu, 3), variantes_geradas=len(tc) or None,
                    tempo_total_s=round(time.perf_counter() - self.t0, 2))

    def progresso_dict(self, desde: int = 0) -> dict:
        """GET /progresso: estado + histórico por geração (a partir de `desde`) + frente."""
        with self.trava:
            return dict(self.estado_dict(), parametros_espaco=self.cfg.espaco.descrever(),
                        **self.progresso.dict(desde))

    def estado_dict(self) -> dict:
        with self.trava:
            d = dict(versao=pr.VERSAO, estado=self.estado, rodada=self.rodada,
                     parametros=list(self.cfg.espaco.nomes),
                     geracao=getattr(self, "geracao", 0),
                     geracoes=self.sessao.info["geracoes"],
                     avaliacoes=sum(len(p) for p, _, _ in self.sessao.problema.registro),
                     no_cache=len(self.memo), erro=self.erro, arquivo=self.arquivo,
                     algoritmo=self.sessao.nome_alg, **self.tempos())
            if self.estado == "gerando":
                d.update(lote=len(self.lote), faltam=len(self.pendentes) + sum(
                    1 for i in self.atribuidos if i not in self.recebidos))
            return d


def esperar(otim: Otimizacao, timeout: float = 600.0):
    """Espera a avaliação em curso (para testes)."""
    f = otim._futuro
    if f is not None:
        f.result(timeout)


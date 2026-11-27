// Gráficos da janela de Otimização (4f2). Sem dependência além do plotly.js.
//
// dados = resposta do GET /progresso (ou um resultado.json convertido pelo C#) +
//   unidades: {"modulo.metrica": "unidade"}, rodando: bool
// objetivos[j] = {nome, sentido}; historico[g] = {geracao, avaliacoes, viaveis, n_frente,
//   melhor: [por objetivo], hipervolume}; frente[i] = {indice, geracao, cluster,
//   objetivos: [por objetivo, unidades originais], parametros: {nome: valor}}
// parametros_espaco = [{nome, tipo, minimo, maximo, opcoes?}]
"use strict";

const otimiza = (() => {
  const CORES = ["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd",
                 "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22", "#17becf"];
  const CONFIG = { displaylogo: false, responsive: true,
                   modeBarButtonsToRemove: ["select2d", "lasso2d", "toImage"] };
  let dados = null;
  let sel = null;            // índice (no histórico de avaliações) da solução escolhida
  let aba = "conv";
  let eixos = [0, 1, 2];     // objetivos nos eixos x, y, z
  let tres = false;          // Pareto em 3D
  let contador = 0;
  let ultimaChave = "";

  const $ = (id) => document.getElementById(id);

  // ---------------------------------------------------------------- ponte com o C#
  function enviar(obj) {
    const txt = JSON.stringify(obj);
    if (window.eto && typeof window.eto.postMessage === "function") window.eto.postMessage(txt);
    else document.title = "otimiza:" + (++contador) + ":" + txt;   // alternativa (DocumentTitleChanged)
  }

  // ---------------------------------------------------------------- formatação
  function fmt(x) {
    if (x === null || x === undefined || Number.isNaN(x)) return "–";
    if (typeof x !== "number") return String(x);
    const a = Math.abs(x);
    if (a !== 0 && (a >= 1e5 || a < 1e-3)) return x.toExponential(3);
    return x.toLocaleString("pt-BR", { maximumFractionDigits: a >= 100 ? 1 : a >= 1 ? 3 : 4 });
  }

  function unidade(nome) {
    const u = (dados && dados.unidades && dados.unidades[nome]) || "";
    return u ? " [" + u + "]" : "";
  }

  function rotulo(j) {
    const o = dados.objetivos[j];
    return o.nome + unidade(o.nome) + " (" + o.sentido + ")";
  }

  function cor(c) { return CORES[((c % CORES.length) + CORES.length) % CORES.length]; }

  // ---------------------------------------------------------------- cabeçalho
  function cabecalho() {
    const m = dados.objetivos.length;
    $("titulo").textContent = m === 1 ? "Otimização (1 objetivo)" : "Otimização (" + m + " objetivos)";
    const est = { gerando: "gerando variantes no GH", avaliando: "avaliando na GPU", fim: "terminada",
                  erro: "erro", livre: "servidor livre", arquivo: "resultado gravado" }[dados.estado]
                || dados.estado;
    $("estado").textContent = est + (dados.algoritmo ? " · " + dados.algoritmo : "")
      + (dados.estado === "arquivo" && dados.arquivo ? " · " + dados.arquivo : "");
    const partes = [];
    if (dados.geracoes) partes.push("geração " + Math.min(dados.geracoes_registradas, dados.geracoes)
                                    + " de " + dados.geracoes);
    if (dados.avaliacoes != null) partes.push(dados.avaliacoes + " avaliações");
    partes.push((m === 1 ? "melhores: " : "na frente: ") + dados.n_frente);
    if (dados.erro) partes.push("ERRO: " + String(dados.erro).split("\n").slice(-2).join(" "));
    $("numeros").textContent = partes.join(" · ");
  }

  // ---------------------------------------------------------------- gráficos
  function semDados(texto) {
    Plotly.purge($("grafico"));
    $("grafico").style.display = "none";
    $("vazio").style.display = "";
    if (texto) $("vazio").textContent = texto;
  }

  function desenhar(traces, layout) {
    const g = $("grafico");
    g.style.display = "";
    $("vazio").style.display = "none";
    // tamanho do contêiner (sem isso o plotly fixa 450 px e invade a tabela)
    const base = { margin: { l: 70, r: 20, t: 16, b: 56 }, font: { family: "Segoe UI, sans-serif", size: 12 },
                   paper_bgcolor: "#fff", plot_bgcolor: "#fff", showlegend: false, uirevision: aba + tres,
                   height: Math.max(150, g.clientHeight), width: Math.max(200, g.clientWidth) };
    Plotly.react($("grafico"), traces, Object.assign(base, layout), CONFIG);
  }

  function convergencia() {
    $("controles").innerHTML = "";
    const h = dados.historico || [];
    if (!h.length) return semDados(dados.estado === "arquivo"
      ? "Resultado gravado: sem histórico por geração (só a frente final)."
      : "A curva aparece depois da 1ª geração.");
    const g = h.map((x) => x.geracao + 1);
    const m = dados.objetivos.length;
    if (m === 1) {
      desenhar([{ x: g, y: h.map((x) => x.melhor[0]), mode: "lines+markers", line: { color: CORES[0] },
                  hovertemplate: "geração %{x}<br>melhor %{y:.4g}<extra></extra>" }],
               { xaxis: { title: "geração", dtick: g.length > 25 ? undefined : 1 },
                 yaxis: { title: "melhor " + rotulo(0) } });
    } else {
      desenhar([{ x: g, y: h.map((x) => x.hipervolume), mode: "lines+markers", line: { color: CORES[0] },
                  hovertemplate: "geração %{x}<br>hipervolume %{y:.4g}<extra></extra>" }],
               { xaxis: { title: "geração", dtick: g.length > 25 ? undefined : 1 },
                 yaxis: { title: "hipervolume (normalizado)", rangemode: "tozero" } });
    }
  }

  function seletorEixo(rotuloTxt, j, aoMudar) {
    const s = document.createElement("select");
    dados.objetivos.forEach((o, k) => s.add(new Option(o.nome, k, false, k === j)));
    s.onchange = () => aoMudar(parseInt(s.value, 10));
    const l = document.createElement("label");
    l.append(rotuloTxt + " ", s);
    return l;
  }

  function pareto() {
    const F = dados.frente || [];
    const m = dados.objetivos.length;
    const c = $("controles");
    c.innerHTML = "";
    if (!F.length) return semDados("Nenhuma solução viável ainda.");
    const custom = F.map((s) => s.indice);
    const cores = F.map((s) => cor(s.cluster));
    const hover = F.map((s) => s.objetivos.map((v, j) => dados.objetivos[j].nome + " = " + fmt(v)).join("<br>")
      + "<br>geração " + (s.geracao + 1) + " · cluster " + (s.cluster + 1));
    const iSel = F.findIndex((s) => s.indice === sel);
    if (m === 1) {
      const y = F.map((s) => s.objetivos[0]);
      const tr = [{ x: F.map((_, i) => i + 1), y, type: "scatter", mode: "markers", customdata: custom,
                    marker: { size: 9, color: CORES[0] }, text: hover, hovertemplate: "%{text}<extra>#%{x}</extra>" }];
      if (iSel >= 0) tr.push({ x: [iSel + 1], y: [y[iSel]], mode: "markers", hoverinfo: "skip",
                               marker: { size: 16, color: "rgba(0,0,0,0)", line: { color: "#000", width: 2 } } });
      return desenhar(tr, { xaxis: { title: "posição (1 = melhor)", dtick: 1 }, yaxis: { title: rotulo(0) } });
    }
    // outro nº de objetivos: volta ao padrão (com 2, o z não é usado)
    if (eixos.slice(0, m >= 3 ? 3 : 2).some((j) => j >= m)) eixos = [0, 1, 2];
    if (m >= 3) {
      const b = document.createElement("label");
      const cb = document.createElement("input");
      cb.type = "checkbox"; cb.checked = tres;
      cb.onchange = () => { tres = cb.checked; pareto(); };
      b.append(cb, " 3D");
      c.append(b);
    }
    c.append(seletorEixo("x", eixos[0], (k) => { eixos[0] = k; pareto(); }),
             seletorEixo("y", eixos[1], (k) => { eixos[1] = k; pareto(); }));
    if (m >= 3 && tres) c.append(seletorEixo("z", eixos[2], (k) => { eixos[2] = k; pareto(); }));
    const v = (j) => F.map((s) => s.objetivos[j]);
    const [jx, jy, jz] = eixos;
    if (m >= 3 && tres) {
      const tr = [{ type: "scatter3d", mode: "markers", x: v(jx), y: v(jy), z: v(jz), customdata: custom,
                    text: hover, hovertemplate: "%{text}<extra></extra>",
                    marker: { size: 4, color: cores, line: { width: 0 } } }];
      if (iSel >= 0) tr.push({ type: "scatter3d", mode: "markers", hoverinfo: "skip",
                               x: [v(jx)[iSel]], y: [v(jy)[iSel]], z: [v(jz)[iSel]],
                               marker: { size: 9, color: "rgba(0,0,0,0)", line: { color: "#000", width: 3 } } });
      return desenhar(tr, { margin: { l: 0, r: 0, t: 0, b: 0 },
                            scene: { xaxis: { title: rotulo(jx) }, yaxis: { title: rotulo(jy) },
                                     zaxis: { title: rotulo(jz) } } });
    }
    const tr = [{ type: "scatter", mode: "markers", x: v(jx), y: v(jy), customdata: custom, text: hover,
                  hovertemplate: "%{text}<extra></extra>", marker: { size: 9, color: cores, opacity: 0.85 } }];
    if (iSel >= 0) tr.push({ type: "scatter", mode: "markers", x: [v(jx)[iSel]], y: [v(jy)[iSel]], hoverinfo: "skip",
                             marker: { size: 17, color: "rgba(0,0,0,0)", line: { color: "#000", width: 2 } } });
    desenhar(tr, { xaxis: { title: rotulo(jx), zeroline: false }, yaxis: { title: rotulo(jy), zeroline: false } });
  }

  function paralelas() {
    $("controles").innerHTML = "";
    const F = dados.frente || [];
    if (F.length < 2) return semDados("Coordenadas paralelas a partir de 2 soluções.");
    const dims = dados.objetivos.map((o, j) => ({ label: o.nome + unidade(o.nome), values: F.map((s) => s.objetivos[j]) }));
    for (const p of (dados.parametros_espaco || [])) {
      const vals = F.map((s) => s.parametros[p.nome]);
      if (p.tipo === "categorico") {
        const ops = (p.opcoes || []).map(String);
        dims.push({ label: p.nome, values: vals.map((x) => ops.indexOf(String(x))),
                    tickvals: ops.map((_, i) => i), ticktext: ops, range: [0, Math.max(1, ops.length - 1)] });
      } else {
        dims.push({ label: p.nome, values: vals.map(Number),
                    range: p.minimo != null && p.maximo != null ? [p.minimo, p.maximo] : undefined });
      }
    }
    const nCl = Math.max(1, ...F.map((s) => s.cluster + 1));
    const escala = Array.from({ length: nCl }, (_, i) => [nCl === 1 ? 0 : i / (nCl - 1), cor(i)]);
    if (nCl === 1) escala.push([1, cor(0)]);
    desenhar([{ type: "parcoords", dimensions: dims,
                line: { color: F.map((s) => s.cluster), colorscale: escala, cmin: 0, cmax: Math.max(1, nCl - 1) } }],
             { margin: { l: 60, r: 60, t: 50, b: 24 } });
  }

  function graficos() {
    if (!dados || !dados.objetivos || !dados.objetivos.length) return semDados();
    ({ conv: convergencia, pareto, paralelas })[aba]();
  }

  // ---------------------------------------------------------------- tabela e seleção
  function tabela() {
    const F = dados.frente || [];
    const nomesP = F.length ? Object.keys(F[0].parametros) : [];
    const cab = ["#", ...dados.objetivos.map((o) => o.nome + unidade(o.nome)), ...nomesP, "geração"];
    const linhas = F.map((s, i) => "<tr data-i='" + s.indice + "'" + (s.indice === sel ? " class='sel'" : "") + ">"
      + "<td><span class='bolinha' style='background:" + cor(s.cluster) + "'></span>" + (i + 1) + "</td>"
      + s.objetivos.map((v) => "<td>" + fmt(v) + "</td>").join("")
      + nomesP.map((k) => "<td>" + fmt(s.parametros[k]) + "</td>").join("")
      + "<td>" + (s.geracao + 1) + "</td></tr>");
    $("tabela").innerHTML = "<table><thead><tr>" + cab.map((t) => "<th>" + t + "</th>").join("")
      + "</tr></thead><tbody>" + linhas.join("") + "</tbody></table>";
  }

  function barra() {
    const s = (dados && dados.frente || []).find((x) => x.indice === sel);
    $("aplicar").disabled = !s || !!dados.rodando;
    $("aplicar").title = dados && dados.rodando ? "espere a otimização terminar" : "";
    $("selecao").textContent = s
      ? "Solução escolhida: " + dados.objetivos.map((o, j) => o.nome + " = " + fmt(s.objetivos[j])).join(" · ")
      : "Clique numa solução (no gráfico ou na tabela).";
  }

  function selecionar(indice) {
    sel = indice;
    for (const tr of document.querySelectorAll("#tabela tbody tr")) {
      const on = Number(tr.dataset.i) === sel;
      tr.classList.toggle("sel", on);
      if (on) tr.scrollIntoView({ block: "nearest" });
    }
    if (aba === "pareto") pareto();
    barra();
  }

  // ---------------------------------------------------------------- entrada
  function atualizar(novos) {
    // mesma rodada e nada novo: só o cabeçalho (evita redesenhar a cada consulta)
    const chave = [novos.rodada, novos.estado, novos.geracoes_registradas, novos.n_frente,
                   (novos.frente || []).length, novos.rodando, novos.arquivo].join("|");
    const mudouRodada = !dados || dados.rodada !== novos.rodada || dados.arquivo !== novos.arquivo;
    dados = novos;
    if (mudouRodada) sel = null;
    cabecalho();
    if (chave === ultimaChave) return;
    ultimaChave = chave;
    if (sel !== null && !(dados.frente || []).some((s) => s.indice === sel)) sel = null;
    graficos();
    tabela();
    barra();
  }

  function iniciar() {
    for (const b of document.querySelectorAll("#abas button"))
      b.onclick = () => {
        aba = b.dataset.aba;
        for (const o of document.querySelectorAll("#abas button")) o.classList.toggle("ativa", o === b);
        graficos();
      };
    $("tabela").onclick = (e) => {
      const tr = e.target.closest("tr[data-i]");
      if (tr) selecionar(Number(tr.dataset.i));
    };
    $("aplicar").onclick = () => {
      const s = (dados.frente || []).find((x) => x.indice === sel);
      if (s) enviar({ acao: "aplicar", indice: s.indice, parametros: s.parametros });
    };
    semDados();
    enviar({ acao: "pronto", motor: navigator.userAgent });
  }

  // o plotly liga o evento de clique no próprio div depois do 1º desenho
  const obs = new MutationObserver(() => {
    const g = $("grafico");
    if (g && g.on && !g._otimizaClique) {
      g._otimizaClique = true;
      g.on("plotly_click", (ev) => {
        const p = ev.points && ev.points[0];
        if (p && p.customdata !== undefined) selecionar(Number(p.customdata));
      });
    }
  });
  let tempoRedim = 0;
  window.addEventListener("resize", () => {          // janela do Rhino redimensionada
    clearTimeout(tempoRedim);
    tempoRedim = setTimeout(() => { if (dados) graficos(); }, 150);
  });
  window.addEventListener("DOMContentLoaded", () => {
    obs.observe($("grafico"), { childList: true });
    iniciar();
  });

  return { atualizar, selecionar, enviar };
})();

/* Stock Watcher frontend: NSE Kenya (Nairobi Securities Exchange) */
(() => {
  "use strict";

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
  const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

  const PERIODS = ["1M", "2M", "3M", "6M", "1Y"];
  const PALETTE = ["#4f8cff", "#f5b83d", "#1fbf75", "#f0525a", "#b07cff", "#2dd4bf", "#fb923c", "#e879f9", "#a3e635", "#38bdf8", "#facc15", "#94a3b8"];

  const state = {
    period: "2M",
    symbol: null,
    watch: [],
    stock: null,
    portfolio: null,
    allocMode: "stock",
    charts: [],
  };

  // ---------------------------------------------------------------- formatting
  const kes = new Intl.NumberFormat("en-KE", { style: "currency", currency: "KES", minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const kes0 = new Intl.NumberFormat("en-KE", { style: "currency", currency: "KES", maximumFractionDigits: 0 });
  const num = new Intl.NumberFormat("en-KE", { maximumFractionDigits: 2 });
  const isNum = (v) => typeof v === "number" && isFinite(v);
  const money = (v, big = false) => (isNum(v) ? (big && Math.abs(v) >= 1e5 ? kes0 : kes).format(v) : "—");
  const n2 = (v) => (isNum(v) ? num.format(v) : "—");
  const pct = (v, sign = true) => (isNum(v) ? `${sign && v > 0 ? "+" : ""}${v.toFixed(2)}%` : "—");
  const cls = (v) => (isNum(v) ? (v > 0 ? "up" : v < 0 ? "down" : "") : "");
  const signed = (v, whole = false) => (isNum(v) ? `${v > 0 ? "+" : ""}${whole ? kes0.format(v) : money(v)}` : "—");
  const whole = (v) => (isNum(v) ? kes0.format(v) : "—");
  const compact = (v) => {
    if (!isNum(v)) return "—";
    for (const [d, u] of [[1e12, "T"], [1e9, "B"], [1e6, "M"], [1e3, "K"]]) if (v >= d) return `${(v / d).toFixed(2)} ${u}`;
    return v.toFixed(0);
  };
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const trendCls = (t) => (t?.startsWith("Uptrend") ? "up" : t?.startsWith("Downtrend") ? "down" : "");
  const rsiCls = (r) => (isNum(r) ? (r >= 70 ? "down" : r <= 30 ? "up" : "") : "");

  function toast(msg) {
    const t = $("#toast");
    t.textContent = msg;
    t.hidden = false;
    clearTimeout(toast._t);
    toast._t = setTimeout(() => (t.hidden = true), 2600);
  }

  // ---------------------------------------------------------------- api
  async function api(path, opts = {}) {
    const res = await fetch(path, {
      ...opts,
      headers: opts.body ? { "Content-Type": "application/json" } : undefined,
    });
    if (!res.ok) {
      let detail = res.statusText;
      try { detail = (await res.json()).detail || detail; } catch {}
      throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
    }
    return res.json();
  }

  /** Stream a Markdown response from an AI endpoint into `out`. */
  async function streamAI(path, out, statusEl, body) {
    out.innerHTML = `<div class="thinking">Analyzing the data${body ? "" : " and checking recent news"}…</div>`;
    if (statusEl) statusEl.textContent = "";
    const started = Date.now();
    let text = "";
    let pending = false;
    const render = () => {
      pending = false;
      out.innerHTML = DOMPurify.sanitize(marked.parse(text));
      $$("a", out).forEach((a) => { a.target = "_blank"; a.rel = "noopener"; });
    };
    try {
      const res = await fetch(path, {
        method: "POST",
        headers: body ? { "Content-Type": "application/json" } : undefined,
        body: body ? JSON.stringify(body) : undefined,
      });
      if (!res.ok) {
        let detail = res.statusText;
        try { detail = (await res.json()).detail || detail; } catch {}
        throw new Error(detail);
      }
      const reader = res.body.getReader();
      const dec = new TextDecoder();
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        text += dec.decode(value, { stream: true });
        if (!pending) { pending = true; requestAnimationFrame(render); }
      }
      text += dec.decode();
      render();
      if (statusEl) statusEl.textContent = `Generated ${new Date().toLocaleTimeString("en-KE", { hour: "2-digit", minute: "2-digit" })} · ${((Date.now() - started) / 1000).toFixed(0)}s`;
    } catch (e) {
      out.innerHTML = `<p class="error">${esc(e.message)}</p>`;
    }
  }

  // ---------------------------------------------------------------- search
  function attachSearch(input, onPick) {
    const dd = input.parentElement.querySelector(".dropdown");
    let items = [];
    let sel = 0;
    let timer;
    let seq = 0;

    const close = () => { dd.hidden = true; };
    const draw = () => {
      if (!input.value.trim()) return close();
      const q = input.value.trim().toUpperCase();
      const opts = items.map((it, i) =>
        `<div class="opt ${i === sel ? "sel" : ""}" data-i="${i}"><b>${esc(it.symbol)}</b><span>${esc(it.name)}</span>${it.suspended ? `<em class="susp">suspended</em>` : ""}</div>`).join("");
      dd.innerHTML = opts || `<div class="none">No match. Press Enter to try “${esc(q)}”.</div>`;
      dd.hidden = false;
    };
    const pick = (i) => {
      const q = input.value.trim().toUpperCase();
      const it = items[i] || (q ? { symbol: q, name: q } : null);
      if (!it) return;
      close();
      onPick(it);
    };

    input.addEventListener("input", () => {
      clearTimeout(timer);
      timer = setTimeout(async () => {
        const q = input.value.trim();
        if (!q) { items = []; return close(); }
        const my = ++seq;
        try {
          const res = await api(`/api/search?q=${encodeURIComponent(q)}`);
          if (my !== seq) return;
          items = res; sel = 0; draw();
        } catch { /* ignore */ }
      }, 120);
    });
    input.addEventListener("keydown", (e) => {
      if (e.key === "ArrowDown") { sel = Math.min(sel + 1, items.length - 1); draw(); e.preventDefault(); }
      else if (e.key === "ArrowUp") { sel = Math.max(sel - 1, 0); draw(); e.preventDefault(); }
      else if (e.key === "Enter") { e.preventDefault(); pick(sel); }
      else if (e.key === "Escape") { close(); input.blur(); }
    });
    dd.addEventListener("mousedown", (e) => {
      const opt = e.target.closest(".opt");
      if (opt) { e.preventDefault(); pick(+opt.dataset.i); }
    });
    input.addEventListener("blur", () => setTimeout(close, 100));
    input.addEventListener("focus", () => items.length && draw());
  }

  // ---------------------------------------------------------------- sparkline
  function spark(values, w = 64, h = 24) {
    if (!values || values.length < 2) return "";
    const min = Math.min(...values), max = Math.max(...values);
    const span = max - min || 1;
    const pts = values.map((v, i) => `${(i / (values.length - 1)) * w},${h - 2 - ((v - min) / span) * (h - 4)}`).join(" ");
    const color = values[values.length - 1] >= values[0] ? css("--up") : css("--down");
    return `<svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}"><polyline points="${pts}" fill="none" stroke="${color}" stroke-width="1.5" stroke-linejoin="round"/></svg>`;
  }

  // ---------------------------------------------------------------- charts
  function baseChart(el, extra = {}) {
    const c = LightweightCharts.createChart(el, {
      autoSize: true,
      layout: { background: { type: "solid", color: "transparent" }, textColor: css("--muted"), fontFamily: "Inter, sans-serif", fontSize: 11 },
      grid: { vertLines: { color: "rgba(34,43,59,.5)" }, horzLines: { color: "rgba(34,43,59,.5)" } },
      rightPriceScale: { borderColor: css("--border") },
      timeScale: { borderColor: css("--border"), fixLeftEdge: true, fixRightEdge: true },
      crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
      localization: { priceFormatter: (p) => num.format(p) },
      ...extra,
    });
    state.charts.push(c);
    return c;
  }

  function clearCharts() {
    state.charts.forEach((c) => c.remove());
    state.charts = [];
  }

  function periodSeg(el, current, onChange) {
    el.innerHTML = PERIODS.map((p) => `<button data-p="${p}" class="${p === current ? "active" : ""}">${p}</button>`).join("");
    el.onclick = (e) => {
      const b = e.target.closest("button");
      if (b) onChange(b.dataset.p);
    };
  }

  // ---------------------------------------------------------------- header: NASI + market status
  async function loadIndices() {
    try {
      const rows = await api("/api/indices");
      $("#indices").innerHTML = rows.map((r) =>
        `<span class="idx" title="NSE All Share Index"><b>${esc(r.name)}</b><span class="mono">${n2(r.price)}</span><span class="mono ${cls(r.day_change_pct)}">${pct(r.day_change_pct)}</span></span>`).join("");
    } catch { /* non-critical */ }
  }

  // NSE Kenya regular session: 09:00–15:00 EAT (UTC+3), Mon–Fri. Exchange holidays not accounted for.
  function marketOpen() {
    const now = new Date();
    const eat = new Date(now.getTime() + (now.getTimezoneOffset() + 180) * 60000);
    const mins = eat.getHours() * 60 + eat.getMinutes();
    return eat.getDay() >= 1 && eat.getDay() <= 5 && mins >= 9 * 60 && mins <= 15 * 60;
  }

  function updateMarketStatus() {
    const open = marketOpen();
    const el = $("#mkt-status");
    el.innerHTML = `<span class="mkt ${open ? "open" : ""}">${open ? "Market open" : "Market closed"}</span>`;
    el.title = "NSE regular session 09:00–15:00 EAT, Mon–Fri (exchange holidays not accounted for)";
  }

  // ---------------------------------------------------------------- watchlist
  async function loadWatchlist() {
    try {
      state.watch = await api("/api/watchlist");
    } catch (e) {
      toast(`Watchlist: ${e.message}`);
    }
    renderWatchlist();
    if (!state.symbol && currentRoute().view === "overview") renderOverviewTable();
  }

  function sortedWatch() {
    const key = { day: "day_change_pct", m1: "return_1m_pct", m2: "return_2m_pct", rsi: "rsi14" }[$("#watch-sort").value];
    const list = [...state.watch];
    if (key) list.sort((a, b) => (b[key] ?? -1e9) - (a[key] ?? -1e9));
    return list;
  }

  function renderWatchlist() {
    const ul = $("#watchlist");
    if (!state.watch.length) {
      ul.innerHTML = `<li class="empty">Your watchlist is empty. Search for a stock above and add it.</li>`;
      return;
    }
    ul.innerHTML = sortedWatch().map((w) => `
      <li data-s="${esc(w.symbol)}" class="${w.symbol === state.symbol ? "active" : ""}">
        <div><div class="sym">${esc(w.symbol)}</div><div class="nm">${esc(w.name)}</div></div>
        <div>${spark(w.spark)}</div>
        <div><div class="px">${w.error ? "—" : n2(w.price)}</div><div class="ch ${cls(w.day_change_pct)}">${w.error ? "no data" : pct(w.day_change_pct)}</div></div>
        <button class="icon-btn rm" title="Remove from watchlist" data-rm="${esc(w.symbol)}">✕</button>
      </li>`).join("");
  }

  $("#watchlist").addEventListener("click", async (e) => {
    const rm = e.target.closest("[data-rm]");
    if (rm) {
      e.stopPropagation();
      await api(`/api/watchlist/${encodeURIComponent(rm.dataset.rm)}`, { method: "DELETE" });
      state.watch = state.watch.filter((w) => w.symbol !== rm.dataset.rm);
      renderWatchlist();
      if (state.symbol === rm.dataset.rm) renderWatchButton();
      return;
    }
    const li = e.target.closest("li[data-s]");
    if (li) location.hash = `#/stock/${encodeURIComponent(li.dataset.s)}`;
  });
  $("#watch-sort").addEventListener("change", renderWatchlist);

  // ---------------------------------------------------------------- overview
  async function showOverview() {
    state.symbol = null;
    renderWatchlist();
    periodSeg($("#overview-period"), state.period, (p) => { state.period = p; showOverview(); });
    renderOverviewTable();
    clearCharts();
    const symbols = state.watch.filter((w) => !w.error).map((w) => w.symbol);
    const el = $("#compare-chart");
    if (!symbols.length) { el.innerHTML = `<div class="empty muted">Add stocks to your watchlist to compare them.</div>`; $("#compare-legend").innerHTML = ""; return; }
    el.innerHTML = "";
    el.classList.add("skeleton");
    let data;
    try {
      data = await api(`/api/compare?symbols=${encodeURIComponent(symbols.join(","))}&period=${state.period}`);
    } catch (e) {
      el.innerHTML = `<p class="error">${esc(e.message)}</p>`;
      return;
    } finally { el.classList.remove("skeleton"); }
    if (currentRoute().view !== "overview") return;
    clearCharts();
    const chart = baseChart(el, { localization: { priceFormatter: (p) => `${p > 0 ? "+" : ""}${p.toFixed(1)}%` } });
    const series = {};
    Object.entries(data).forEach(([s, pts], i) => {
      series[s] = chart.addLineSeries({ color: PALETTE[i % PALETTE.length], lineWidth: 2, priceLineVisible: false, title: s });
      series[s].setData(pts);
    });
    Object.values(series)[0]?.createPriceLine({ price: 0, color: css("--muted"), lineStyle: 2, lineWidth: 1, axisLabelVisible: false });
    chart.timeScale().fitContent();
    $("#compare-legend").innerHTML = Object.keys(data).map((s, i) => {
      const last = data[s].at(-1)?.value;
      return `<span data-s="${esc(s)}"><i style="background:${PALETTE[i % PALETTE.length]}"></i>${esc(s)} <b class="mono ${cls(last)}">${pct(last)}</b></span>`;
    }).join("");
    $("#compare-legend").onclick = (e) => {
      const sp = e.target.closest("[data-s]");
      if (!sp) return;
      const s = series[sp.dataset.s];
      const vis = s.options().visible !== false;
      s.applyOptions({ visible: !vis });
      sp.style.opacity = vis ? 0.4 : 1;
    };
  }

  function renderOverviewTable() {
    const tb = $("#overview-table tbody");
    if (!state.watch.length) { tb.innerHTML = `<tr><td colspan="9" class="muted">Watchlist is empty.</td></tr>`; return; }
    tb.innerHTML = sortedWatch().map((w) => `
      <tr class="row" data-s="${esc(w.symbol)}">
        <td><div class="s1">${esc(w.symbol)}</div><div class="s2">${esc(w.name)}</div></td>
        <td class="r">${n2(w.price)}</td>
        <td class="r ${cls(w.day_change_pct)}">${pct(w.day_change_pct)}</td>
        <td class="r ${cls(w.return_1m_pct)}">${pct(w.return_1m_pct)}</td>
        <td class="r ${cls(w.return_2m_pct)}">${pct(w.return_2m_pct)}</td>
        <td class="r ${cls(w.return_3m_pct)}">${pct(w.return_3m_pct)}</td>
        <td class="r ${rsiCls(w.rsi14)}">${isNum(w.rsi14) ? w.rsi14.toFixed(0) : "—"}</td>
        <td><span class="trend-chip ${trendCls(w.trend)}">${esc(w.trend || "—")}</span></td>
        <td>${spark(w.spark, 96, 26)}</td>
      </tr>`).join("");
  }
  $("#overview-table").addEventListener("click", (e) => {
    const tr = e.target.closest("tr[data-s]");
    if (tr) location.hash = `#/stock/${encodeURIComponent(tr.dataset.s)}`;
  });

  $("#ask-form").addEventListener("submit", (e) => {
    e.preventDefault();
    const q = $("#ask-input").value.trim();
    if (q.length < 2) return;
    const out = $("#ask-out");
    out.hidden = false;
    streamAI("/api/ai/ask", out, null, { question: q });
  });

  // ---------------------------------------------------------------- stock view
  async function showStock(symbol) {
    const changed = symbol !== state.symbol;
    state.symbol = symbol;
    renderWatchlist();
    if (changed) {
      $("#stock-ai").hidden = true;
      $("#about").hidden = true;
      $("#s-sector").hidden = true;
      $("#s-name").textContent = symbol;
      $("#s-symbol").textContent = symbol;
      $("#s-price").textContent = "";
      $("#s-change").textContent = "";
      $("#stats").innerHTML = "";
      $("#position").hidden = true;
      loadMeta(symbol);
    }
    periodSeg($("#stock-period"), state.period, (p) => { state.period = p; showStock(symbol); });
    renderWatchButton();
    $("#price-chart").classList.add("skeleton");
    let data;
    try {
      data = await api(`/api/stock?symbol=${encodeURIComponent(symbol)}&period=${state.period}`);
    } catch (e) {
      $("#price-chart").classList.remove("skeleton");
      clearCharts();
      $("#price-chart").innerHTML = `<div class="empty"><h2>Couldn't load ${esc(symbol)}</h2><p class="muted">${esc(e.message)}. Check the NSE ticker and try again.</p></div>`;
      $("#rsi-chart").innerHTML = "";
      return;
    }
    $("#price-chart").classList.remove("skeleton");
    if (state.symbol !== symbol) return;
    state.stock = data;
    renderStock(data);
    renderPosition(symbol);
  }

  async function loadMeta(symbol) {
    try {
      const m = await api(`/api/meta?symbol=${encodeURIComponent(symbol)}`);
      if (state.symbol !== symbol) return;
      if (m.sector) { $("#s-sector").textContent = m.sector; $("#s-sector").hidden = false; }
      const bits = [
        ["Market cap", isNum(m.market_cap) ? `KES ${compact(m.market_cap)}` : null],
        ["P/E", isNum(m.pe) ? m.pe.toFixed(1) : null],
        ["EPS", isNum(m.eps) ? n2(m.eps) : null],
        ["Dividend / share", isNum(m.dps) ? n2(m.dps) : null],
        ["Div. yield", isNum(m.dividend_yield) ? `${m.dividend_yield.toFixed(2)}%` : null],
        ["Shares out.", isNum(m.shares_outstanding) ? compact(m.shares_outstanding) : null],
      ].filter(([, v]) => v);
      if (!bits.length && !m.summary && !m.industry) return;
      $("#about").innerHTML = `
        <div class="card-head"><h2>Fundamentals</h2>${m.industry ? `<span class="muted small">${esc(m.industry)}</span>` : ""}</div>
        <div class="stats">${bits.map(([k, v]) => `<div class="stat"><div class="k">${k}</div><div class="v" style="font-size:15px">${esc(v)}</div></div>`).join("")}</div>
        ${m.summary ? `<p>${esc(m.summary)}${m.summary.length >= 600 ? "…" : ""}</p>` : ""}`;
      $("#about").hidden = false;
    } catch { /* optional */ }
  }

  function renderWatchButton() {
    const on = state.watch.some((w) => w.symbol === state.symbol);
    const b = $("#btn-watch");
    b.textContent = on ? "★ Watching" : "☆ Watch";
    b.classList.toggle("on", on);
  }

  $("#btn-watch").addEventListener("click", async () => {
    const s = state.symbol;
    const on = state.watch.some((w) => w.symbol === s);
    await api(`/api/watchlist/${encodeURIComponent(s)}`, { method: on ? "DELETE" : "POST" });
    toast(on ? `Removed ${s} from watchlist` : `Added ${s} to watchlist`);
    await loadWatchlist();
    renderWatchButton();
  });

  function renderStock(d) {
    const s = d.stats;
    $("#s-name").textContent = d.name;
    $("#s-symbol").textContent = d.symbol;
    $("#s-price").textContent = money(s.price);
    $("#s-change").className = `chg mono ${cls(s.day_change)}`;
    $("#s-change").textContent = `${signed(s.day_change)} (${pct(s.day_change_pct)})`;
    $("#s-asof").textContent = `as of ${s.as_of}`;
    document.title = `${d.symbol} ${n2(s.price)} · Stock Watcher`;

    const stat = (k, v, sub = "", c = "") => `<div class="stat"><div class="k">${k}</div><div class="v ${c}">${v}</div>${sub ? `<div class="s">${sub}</div>` : ""}</div>`;
    $("#stats").innerHTML = [
      stat(`${s.period} return`, pct(s.period_return_pct), `1M ${pct(s.return_1m_pct)} · 2M ${pct(s.return_2m_pct)}`, cls(s.period_return_pct)),
      stat("Trend", `<span class="trend-chip ${trendCls(s.trend)}">${esc(s.trend)}</span>`, `SMA20 ${n2(s.sma20)} · SMA50 ${n2(s.sma50)}`),
      stat("RSI (14)", isNum(s.rsi14) ? s.rsi14.toFixed(1) : "—", s.rsi14 >= 70 ? "Overbought zone" : s.rsi14 <= 30 ? "Oversold zone" : "Neutral zone", rsiCls(s.rsi14)),
      stat(`${s.period} range`, `${n2(s.period_low)} – ${n2(s.period_high)}`, `52W ${n2(s.low_52w)} – ${n2(s.high_52w)}`),
      stat("From 52W high", pct(s.pct_from_52w_high), `Today ${n2(s.day_low)} – ${n2(s.day_high)}`, cls(s.pct_from_52w_high)),
      stat("Volatility (ann.)", pct(s.volatility_ann_pct, false), `Best ${pct(s.best_day_pct)} · Worst ${pct(s.worst_day_pct)}`),
      stat("Max drawdown", pct(s.max_drawdown_pct), `within ${s.period}`, "down"),
      stat("Avg volume (10d)", compact(s.avg_volume), isNum(s.volume_ratio_5d) ? `Last 5d at ${s.volume_ratio_5d.toFixed(2)}× avg` : ""),
      stat("Up / down days", `${s.up_days} / ${s.down_days}`, `in ${s.period}`),
    ].join("");

    drawStockCharts(d.points);
  }

  // The source has daily closes only, so price is an area line rather than candles.
  function drawStockCharts(points) {
    clearCharts();
    $("#price-chart").innerHTML = "";
    $("#rsi-chart").innerHTML = "";
    const up = css("--up"), down = css("--down");

    const chart = baseChart($("#price-chart"));
    const rising = points.at(-1).close >= points[0].close;
    const price = chart.addAreaSeries({
      lineColor: rising ? up : down, lineWidth: 2, bottomColor: "rgba(0,0,0,0)",
      topColor: rising ? "rgba(31,191,117,.28)" : "rgba(240,82,90,.28)",
    });
    price.setData(points.map(({ time, close }) => ({ time, value: close })));
    price.priceScale().applyOptions({ scaleMargins: { top: 0.06, bottom: 0.24 } });

    const vol = chart.addHistogramSeries({ priceFormat: { type: "volume" }, priceScaleId: "vol", lastValueVisible: false, priceLineVisible: false });
    chart.priceScale("vol").applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } });
    vol.setData(points.flatMap((p, i) => (p.volume == null ? [] : [{
      time: p.time, value: p.volume,
      color: p.close >= (points[i - 1]?.close ?? p.close) ? "rgba(31,191,117,.35)" : "rgba(240,82,90,.35)",
    }])));

    const line = (key, color) => {
      const s = chart.addLineSeries({ color, lineWidth: 1.5, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
      s.setData(points.filter((p) => p[key] != null).map((p) => ({ time: p.time, value: p[key] })));
      return s;
    };
    const sma20 = line("sma20", css("--sma20"));
    const sma50 = line("sma50", css("--sma50"));

    const applyToggles = () => {
      sma20.applyOptions({ visible: $("#t-sma20").checked });
      sma50.applyOptions({ visible: $("#t-sma50").checked });
      vol.applyOptions({ visible: $("#t-vol").checked });
    };
    ["#t-sma20", "#t-sma50", "#t-vol"].forEach((id) => ($(id).onchange = applyToggles));
    applyToggles();
    chart.timeScale().fitContent();

    // RSI pane
    const rsiChart = baseChart($("#rsi-chart"), { timeScale: { visible: false, fixLeftEdge: true, fixRightEdge: true } });
    const rsiLine = rsiChart.addLineSeries({ color: css("--accent-2"), lineWidth: 1.5, priceLineVisible: false });
    rsiLine.setData(points.filter((p) => p.rsi != null).map((p) => ({ time: p.time, value: p.rsi })));
    [70, 30].forEach((p) => rsiLine.createPriceLine({ price: p, color: p === 70 ? down : up, lineStyle: 2, lineWidth: 1, axisLabelVisible: true }));
    rsiChart.priceScale("right").applyOptions({ autoScale: false, scaleMargins: { top: 0.1, bottom: 0.1 } });
    rsiLine.applyOptions({ autoscaleInfoProvider: () => ({ priceRange: { minValue: 0, maxValue: 100 } }) });
    rsiChart.timeScale().fitContent();

    // keep panes in sync
    let syncing = false;
    const sync = (from, to) => from.timeScale().subscribeVisibleLogicalRangeChange((r) => {
      if (syncing || !r) return;
      syncing = true; to.timeScale().setVisibleLogicalRange(r); syncing = false;
    });
    sync(chart, rsiChart);
    sync(rsiChart, chart);

    // Readout for the hovered day
    const idx = new Map(points.map((p, i) => [p.time, i]));
    const readout = (i) => {
      const p = points[i];
      if (!p) return;
      const prev = points[i - 1];
      const chg = prev ? ((p.close - prev.close) / prev.close) * 100 : null;
      $("#ohlc").innerHTML = `${p.time} &nbsp;Close <b class="${cls(chg)}">${n2(p.close)}</b><b class="${cls(chg)}">${pct(chg)}</b>${p.volume != null ? `Vol <b>${compact(p.volume)}</b>` : ""}`;
    };
    readout(points.length - 1);
    chart.subscribeCrosshairMove((e) => readout(e.time ? idx.get(e.time) : points.length - 1));
  }

  async function renderPosition(symbol) {
    const el = $("#position");
    try {
      const pf = state.portfolio || (state.portfolio = await api("/api/portfolio"));
      const h = pf.holdings.find((x) => x.symbol === symbol);
      if (!h || state.symbol !== symbol) { el.hidden = true; return; }
      const kv = (k, v, c = "") => `<div><div class="k">${k}</div><div class="v ${c}">${v}</div></div>`;
      el.innerHTML = [
        kv("Your position", `${n2(h.qty)} shares`),
        kv("Avg cost", money(h.avg_price)),
        kv("Value", money(h.value)),
        kv("P&L", `${signed(h.pnl)} (${pct(h.pnl_pct)})`, cls(h.pnl)),
        kv("Today", signed(h.day_pnl), cls(h.day_pnl)),
        kv("Dividends / yr", money(h.annual_dividend)),
        kv("Weight", pct(h.weight_pct, false)),
      ].join("");
      el.hidden = false;
    } catch { el.hidden = true; }
  }

  $("#btn-ai").addEventListener("click", () => {
    const card = $("#stock-ai");
    card.hidden = false;
    card.scrollIntoView({ behavior: "smooth", block: "start" });
    streamAI(`/api/ai/stock?symbol=${encodeURIComponent(state.symbol)}&period=${state.period}`, $(".ai-out", card), $("#stock-ai-status"));
  });

  $("#btn-buy").addEventListener("click", () => openLotDialog({ symbol: state.symbol, price: state.stock?.symbol === state.symbol ? state.stock.stats.price : undefined }));

  // ---------------------------------------------------------------- portfolio
  let allocChart;

  async function showPortfolio() {
    state.symbol = null;
    renderWatchlist();
    $("#pf-tiles").innerHTML = Array(5).fill(`<div class="tile skeleton" style="height:84px"></div>`).join("");
    try {
      state.portfolio = await api("/api/portfolio");
    } catch (e) {
      $("#pf-tiles").innerHTML = `<p class="error">${esc(e.message)}</p>`;
      return;
    }
    renderPortfolio();
  }

  function renderPortfolio() {
    const pf = state.portfolio;
    const empty = !pf.holdings.length;
    $("#pf-empty").hidden = !empty;
    $("#pf-body").hidden = empty;
    $("#btn-ai-pf").disabled = empty;
    if (empty) return;
    const t = pf.totals, r = pf.risk || {};

    const tile = (k, v, sub = "", c = "") => `<div class="tile"><div class="k">${k}</div><div class="v ${c}">${v}</div><div class="s ${c}">${sub}</div></div>`;
    $("#pf-tiles").innerHTML = [
      tile("Current value", whole(t.value), `${t.positions} positions`),
      tile("Invested", whole(t.invested)),
      tile("Total P&L", signed(t.pnl, true), pct(t.pnl_pct), cls(t.pnl)),
      tile("Today's P&L", signed(t.day_pnl, true), pct(t.day_pnl_pct), cls(t.day_pnl)),
      tile("2M return", pct(r.portfolio_return_2m_pct), `1M ${pct(r.portfolio_return_1m_pct)}`, cls(r.portfolio_return_2m_pct)),
      tile("Dividends / yr", whole(t.annual_dividend), isNum(t.dividend_yield_pct) ? `${t.dividend_yield_pct.toFixed(2)}% yield` : ""),
    ].join("");

    // risk
    const rk = (k, v, sub = "") => `<div><div class="k">${k}</div><div class="v">${v}</div>${sub ? `<div class="k">${sub}</div>` : ""}</div>`;
    const notes = [];
    if (r.top_holding_pct > 25) notes.push(`Your largest position is ${r.top_holding_pct.toFixed(0)}% of the portfolio.`);
    const topSector = pf.sectors[0];
    if (topSector && topSector.weight_pct > 40) notes.push(`${topSector.sector} makes up ${topSector.weight_pct.toFixed(0)}% of value.`);
    $("#pf-risk").innerHTML = [
      rk("Volatility (ann.)", pct(r.volatility_ann_pct, false)),
      rk("Effective positions", isNum(r.effective_positions) ? r.effective_positions.toFixed(1) : "—", `of ${t.positions} held`),
      rk("Largest position", pct(r.top_holding_pct, false)),
      rk("Largest sector", topSector ? `${pct(topSector.weight_pct, false)}` : "—", topSector ? esc(topSector.sector) : ""),
      r.most_correlated?.length ? `<div class="note">Most correlated pairs: ${r.most_correlated.map((p) => `${esc(p.a)}–${esc(p.b)} (${isNum(p.corr) ? p.corr.toFixed(2) : "—"})`).join(", ")}</div>` : "",
      notes.length ? `<div class="note">${notes.map(esc).join(" ")}</div>` : "",
    ].join("");

    renderAlloc();

    // holdings table
    $("#pf-table tbody").innerHTML = pf.holdings.map((h) => `
      <tr class="row" data-s="${esc(h.symbol)}">
        <td><div class="s1">${esc(h.symbol)}</div><div class="s2">${esc(h.name)} · ${esc(h.sector)}</div></td>
        <td class="r">${n2(h.qty)}</td>
        <td class="r">${n2(h.avg_price)}</td>
        <td class="r">${n2(h.price)}</td>
        <td class="r">${money(h.value)}</td>
        <td class="r ${cls(h.pnl)}">${signed(h.pnl)}<div class="small">${pct(h.pnl_pct)}</div></td>
        <td class="r ${cls(h.day_change_pct)}">${pct(h.day_change_pct)}</td>
        <td class="r ${cls(h.return_2m_pct)}">${pct(h.return_2m_pct)}</td>
        <td class="r">${isNum(h.dividend_yield) ? `${h.dividend_yield.toFixed(1)}%` : "—"}</td>
        <td class="r">${pct(h.weight_pct, false)}</td>
        <td class="r ${rsiCls(h.rsi14)}">${isNum(h.rsi14) ? h.rsi14.toFixed(0) : "—"}</td>
      </tr>
      <tr class="lots" data-lots="${esc(h.symbol)}" hidden><td colspan="11">
        ${h.lots.map((l) => `<div class="lot">
          <span>Bought <span class="mono">${n2(l.qty)}</span> @ <span class="mono">${n2(l.price)}</span></span>
          <span>${esc(l.date || "no date")}</span>
          <span class="grow">${esc(l.note || "")}</span>
          <button data-edit="${esc(l.id)}">Edit</button>
          <button class="del" data-del="${esc(l.id)}">Delete</button>
        </div>`).join("")}
        <div class="lot"><button data-chart="${esc(h.symbol)}">Open chart →</button><button data-add="${esc(h.symbol)}">+ Add lot</button></div>
      </td></tr>`).join("");
  }

  function renderAlloc() {
    const pf = state.portfolio;
    const rows = state.allocMode === "stock"
      ? pf.holdings.filter((h) => isNum(h.value)).map((h) => ({ label: h.symbol, value: h.value }))
      : pf.sectors.map((s) => ({ label: s.sector, value: s.value }));
    const seg = $("#alloc-mode");
    seg.innerHTML = ["stock", "sector"].map((m) => `<button data-m="${m}" class="${m === state.allocMode ? "active" : ""}">By ${m}</button>`).join("");
    seg.onclick = (e) => { const b = e.target.closest("button"); if (b) { state.allocMode = b.dataset.m; renderAlloc(); } };
    allocChart?.destroy();
    const total = rows.reduce((a, r) => a + r.value, 0);
    allocChart = new Chart($("#alloc-chart"), {
      type: "doughnut",
      data: {
        labels: rows.map((r) => r.label),
        datasets: [{ data: rows.map((r) => r.value), backgroundColor: rows.map((_, i) => PALETTE[i % PALETTE.length]), borderColor: css("--panel"), borderWidth: 2 }],
      },
      options: {
        maintainAspectRatio: false,
        cutout: "62%",
        plugins: {
          legend: { position: "right", labels: { color: css("--text"), boxWidth: 10, boxHeight: 10, font: { family: "Inter", size: 12 },
            generateLabels: (c) => c.data.labels.map((l, i) => ({ text: `${l}  ${((rows[i].value / total) * 100).toFixed(1)}%`, fillStyle: PALETTE[i % PALETTE.length], strokeStyle: "transparent", fontColor: css("--text"), index: i })) } },
          tooltip: { callbacks: { label: (ctx) => ` ${money(ctx.raw)} (${((ctx.raw / total) * 100).toFixed(1)}%)` } },
        },
      },
    });
  }

  $("#pf-table").addEventListener("click", async (e) => {
    const t = e.target;
    if (t.dataset.del) {
      if (!confirm("Delete this lot?")) return;
      await api(`/api/holdings/${t.dataset.del}`, { method: "DELETE" });
      toast("Lot deleted");
      return showPortfolio();
    }
    if (t.dataset.edit) {
      const lot = state.portfolio.holdings.flatMap((h) => h.lots).find((l) => l.id === t.dataset.edit);
      return openLotDialog(lot);
    }
    if (t.dataset.chart) { location.hash = `#/stock/${encodeURIComponent(t.dataset.chart)}`; return; }
    if (t.dataset.add) {
      const h = state.portfolio.holdings.find((x) => x.symbol === t.dataset.add);
      return openLotDialog({ symbol: h.symbol, price: h.price });
    }
    const row = t.closest("tr.row");
    if (row) {
      const lots = $(`tr[data-lots="${CSS.escape(row.dataset.s)}"]`);
      lots.hidden = !lots.hidden;
    }
  });

  $("#btn-ai-pf").addEventListener("click", () => {
    const card = $("#pf-ai");
    card.hidden = false;
    card.scrollIntoView({ behavior: "smooth", block: "start" });
    streamAI("/api/ai/portfolio", $(".ai-out", card), $("#pf-ai-status"));
  });
  $("#btn-add-lot").addEventListener("click", () => openLotDialog({}));
  $("#btn-add-first").addEventListener("click", () => openLotDialog({}));

  // ---------------------------------------------------------------- lot dialog
  const dlg = $("#lot-dialog");
  const form = $("#lot-form");
  let editingId = null;

  function openLotDialog(lot) {
    editingId = lot.id || null;
    $("#lot-title").textContent = editingId ? `Edit ${lot.symbol} lot` : "Add holding";
    form.symbol.value = lot.symbol || "";
    form.symbol.readOnly = !!editingId;
    form.qty.value = lot.qty ?? "";
    form.price.value = isNum(lot.price) ? lot.price.toFixed(2) : "";
    form.date.value = lot.date || new Date().toISOString().slice(0, 10);
    form.note.value = lot.note || "";
    $("#lot-error").hidden = true;
    dlg.showModal();
    (lot.symbol ? form.qty : form.symbol).focus();
  }

  attachSearch(form.symbol, async (it) => {
    form.symbol.value = it.symbol;
    form.qty.focus();
    if (!form.price.value) {
      try {
        const d = await api(`/api/stock?symbol=${encodeURIComponent(it.symbol)}&period=1M`);
        if (!form.price.value) form.price.value = d.stats.price.toFixed(2);
      } catch { /* leave blank */ }
    }
  });

  $("#lot-cancel").addEventListener("click", () => dlg.close());
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const body = {
      qty: parseFloat(form.qty.value),
      price: parseFloat(form.price.value),
      date: form.date.value || null,
      note: form.note.value.trim() || null,
    };
    $("#lot-save").disabled = true;
    try {
      if (editingId) {
        await api(`/api/holdings/${editingId}`, { method: "PATCH", body: JSON.stringify(body) });
      } else {
        const symbol = form.symbol.value.trim().toUpperCase();
        await api(`/api/stock?symbol=${encodeURIComponent(symbol)}&period=1M`); // validates the symbol has data
        await api("/api/holdings", { method: "POST", body: JSON.stringify({ symbol, ...body }) });
      }
      dlg.close();
      toast(editingId ? "Holding updated" : "Holding added");
      state.portfolio = null;
      const route = currentRoute();
      if (route.view === "portfolio") showPortfolio();
      else if (route.view === "stock") renderPosition(route.symbol);
    } catch (err) {
      $("#lot-error").textContent = err.message;
      $("#lot-error").hidden = false;
    } finally {
      $("#lot-save").disabled = false;
    }
  });

  // ---------------------------------------------------------------- routing
  function currentRoute() {
    const h = decodeURIComponent(location.hash.slice(1));
    if (h.startsWith("/stock/")) return { view: "stock", symbol: h.slice(7).toUpperCase().replace(/^KE:/, "") };
    if (h === "/portfolio") return { view: "portfolio" };
    return { view: "overview" };
  }

  function route() {
    const r = currentRoute();
    $("#view-overview").hidden = r.view !== "overview";
    $("#view-stock").hidden = r.view !== "stock";
    $("#view-portfolio").hidden = r.view !== "portfolio";
    $$(".tabs a").forEach((a) => a.classList.toggle("active", a.dataset.tab === (r.view === "portfolio" ? "portfolio" : "markets")));
    if (r.view !== "stock") document.title = "Stock Watcher";
    window.scrollTo({ top: 0 });
    if (r.view === "stock") showStock(r.symbol);
    else if (r.view === "portfolio") { clearCharts(); showPortfolio(); }
    else showOverview();
  }

  // ---------------------------------------------------------------- boot
  attachSearch($("#search"), (it) => {
    $("#search").value = "";
    location.hash = `#/stock/${encodeURIComponent(it.symbol)}`;
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "/" && !["INPUT", "TEXTAREA"].includes(document.activeElement.tagName) && !dlg.open) {
      e.preventDefault();
      $("#search").focus();
    }
  });
  window.addEventListener("hashchange", route);

  // Refresh every 10 minutes while the market is open (the data source updates daily).
  setInterval(updateMarketStatus, 60000);
  setInterval(() => {
    if (!marketOpen() || document.hidden) return;
    loadIndices();
    loadWatchlist();
  }, 600000);

  updateMarketStatus();
  loadIndices();
  loadWatchlist().then(route);
})();

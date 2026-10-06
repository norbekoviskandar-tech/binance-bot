"""Live tape tab: real-time prices + trade flow for today's top risers, pushed by Binance WebSockets straight into the browser
(no server hop, no polling). Public streams only, no API key."""

CORE = r"""
const N_TOP = __TOPN__, MIN_Q = 2e7;
const book = {tick: {}, coins: {}, top: [], subs: new Set()};
function coin(s) { return book.coins[s] || (book.coins[s] = {sec: {}, px: {}, peak30: 0, subAt: 0, last: null, pruned: 0}); }
function markSub(s, now) { coin(s).subAt = now; }
function ingestMini(arr) {
  for (const t of arr) {
    if (!t.s.endsWith("USDT") || t.s.indexOf("_") >= 0) continue;
    const c = +t.c, o = +t.o;
    book.tick[t.s] = {c: c, o: o, q: +t.q, chg: (c / o - 1) * 100};
  }
  const list = Object.keys(book.tick).filter(s => book.tick[s].q >= MIN_Q)
    .sort((a, b) => book.tick[b].chg - book.tick[a].chg).slice(0, N_TOP);
  const changed = list.join() !== book.top.join();
  book.top = list;
  return changed;
}
function ingestTrade(d) {
  const c = coin(d.s), sec = Math.floor(d.T / 1000), usd = +d.p * +d.q;
  const b = c.sec[sec] || (c.sec[sec] = {buy: 0, sell: 0, n: 0});
  if (d.m) b.sell += usd; else b.buy += usd;  // m = buyer is the maker -> the aggressor was a SELLER
  b.n++;
  c.px[sec] = +d.p; c.last = +d.p;
  if (sec - c.pruned > 10) {
    for (const k in c.sec) if (+k < sec - 240) delete c.sec[k];
    for (const k in c.px) if (+k < sec - 240) delete c.px[k];
    c.pruned = sec;
  }
}
function sum(c, from, to) {
  let buy = 0, sell = 0;
  for (let t = from; t <= to; t++) { const b = c.sec[t]; if (b) { buy += b.buy; sell += b.sell; } }
  return {buy: buy, sell: sell, tot: buy + sell};
}
function rows(now) {
  const sec = Math.floor(now / 1000), out = [];
  book.top.forEach((s, i) => {
    const tk = book.tick[s], c = coin(s);
    const warm = c.subAt ? (now - c.subAt) / 1000 : 0;
    const w60 = sum(c, sec - 59, sec), w30 = sum(c, sec - 29, sec), p30 = sum(c, sec - 59, sec - 30);
    const w15 = sum(c, sec - 14, sec), p15 = sum(c, sec - 29, sec - 15);
    const normMin = tk.q / 1440;  // an average minute of this coin's traded value
    const pace = warm >= 5 ? w60.tot / (normMin * Math.min(warm, 60) / 60) : null;
    const accel = warm >= 60 && p30.tot > 0 ? w30.tot / p30.tot : null;
    const buyers = w15.tot > 0 ? w15.buy / w15.tot * 100 : null;
    let px60 = null;
    for (let t = sec - 60; t >= sec - 75 && px60 === null; t--) if (c.px[t] !== undefined) px60 = c.px[t];
    const price = c.last !== null ? c.last : tk.c;
    c.peak30 = Math.max(c.peak30, w30.tot);
    const sell = buyers === null ? 0 : (buyers < 35 ? 2 : (buyers < 48 ? 1 : 0));  // heavy selling counts double
    const flip = (w15.buy - w15.sell) < 0 && (p15.buy - p15.sell) > 0 ? 1 : 0;
    const fade = warm >= 90 && c.peak30 > 0 && w30.tot < 0.5 * c.peak30 ? 1 : 0;
    const cool = sell + flip + fade;
    const chg1m = px60 !== null ? (price / px60 - 1) * 100 : null;
    const hot = pace !== null && accel !== null && pace >= 4 && accel >= 1.3 && buyers >= 58 && chg1m > 0;
    out.push({s: s, rank: i + 1, price: price, chg24: tk.chg, chg1m: chg1m, pace: pace, accel: accel, buyers: buyers,
              net: w15.buy - w15.sell, cool: cool, status: cool >= 2 ? "🧊 cooling" : (hot ? "🟢 hot" : (warm < 60 ? "warming up…" : ""))});
  });
  return out;
}
"""

GLUE = r"""
let ws, backoff = 1000, msgs = 0, lastE = 0, shown = 0;
const els = {}, $ = id => document.getElementById(id);
const fmtP = p => p >= 1 ? p.toFixed(p >= 100 ? 2 : 4) : p.toPrecision(4);
const fmtU = x => { const a = Math.abs(x), s = x < 0 ? "-" : "+"; return a >= 1e6 ? s + (a / 1e6).toFixed(2) + "M" : a >= 1e3 ? s + (a / 1e3).toFixed(1) + "k" : s + a.toFixed(0); };
const fmtN = (x, d, suf) => x === null ? "…" : x.toFixed(d) + (suf || "");
function setStatus(t, col) { $("st").textContent = t; $("st").style.color = col; }
function connect() {
  ws = new WebSocket("wss://fstream.binance.com/stream?streams=!miniTicker@arr");
  ws.onopen = () => { backoff = 1000; book.subs.clear(); setStatus("● live", "#0ecb81"); };
  ws.onmessage = ev => {
    msgs++;
    const m = JSON.parse(ev.data), d = m.data;
    if (!d) return;
    if (m.stream === "!miniTicker@arr") { if (ingestMini(d)) syncSubs(); }
    else if (d.e === "aggTrade") { ingestTrade(d); lastE = d.E; }
  };
  ws.onclose = () => { setStatus("● disconnected, retrying…", "#f6465d"); setTimeout(connect, backoff); backoff = Math.min(backoff * 2, 15000); };
  ws.onerror = () => ws.close();
}
function syncSubs() {
  if (!ws || ws.readyState !== 1) return;
  const want = new Set(book.top.map(s => s.toLowerCase() + "@aggTrade"));
  const add = [...want].filter(x => !book.subs.has(x)), del = [...book.subs].filter(x => !want.has(x));
  if (add.length) { ws.send(JSON.stringify({method: "SUBSCRIBE", params: add, id: Date.now()})); add.forEach(x => { book.subs.add(x); markSub(x.split("@")[0].toUpperCase(), Date.now()); }); }
  if (del.length) { ws.send(JSON.stringify({method: "UNSUBSCRIBE", params: del, id: Date.now() + 1})); del.forEach(x => book.subs.delete(x)); }
}
function paint() {
  const r = rows(Date.now()), tb = $("tb"), keep = new Set();
  r.forEach(x => {
    keep.add(x.s);
    let e = els[x.s];
    if (!e) { e = {tr: document.createElement("tr"), c: [], prev: null}; for (let k = 0; k < 11; k++) { const td = document.createElement("td"); e.tr.appendChild(td); e.c.push(td); } els[x.s] = e; }
    const v = [x.rank, x.s.replace("USDT", ""), fmtP(x.price), fmtN(x.chg24, 1, "%"), fmtN(x.chg1m, 2, "%"), fmtN(x.pace, 1, "×"), fmtN(x.accel, 1, "×"),
               fmtN(x.buyers, 0, "%"), fmtU(x.net), x.cool, x.status];
    v.forEach((t, k) => { if (e.c[k].textContent !== String(t)) e.c[k].textContent = t; });
    e.c[3].className = x.chg24 >= 0 ? "up" : "dn";
    e.c[4].className = x.chg1m === null ? "" : (x.chg1m >= 0 ? "up" : "dn");
    e.c[8].className = x.net >= 0 ? "up" : "dn";
    e.c[9].className = x.cool >= 2 ? "dn" : "";
    if (e.prev !== null && x.price !== e.prev) { e.c[2].className = x.price > e.prev ? "flashup" : "flashdn"; setTimeout(() => e.c[2].className = "", 400); }
    e.prev = x.price;
    tb.appendChild(e.tr);
  });
  Object.keys(els).forEach(s => { if (!keep.has(s)) { els[s].tr.remove(); delete els[s]; } });
}
setInterval(paint, 250);
setInterval(() => { shown = msgs; msgs = 0; $("rate").textContent = shown + " msgs/s"; $("age").textContent = lastE ? "last trade " + Math.max(0, Date.now() - lastE) + " ms ago (includes your PC clock offset)" : ""; }, 1000);
connect();
"""

PAGE = """<!doctype html><html><head><meta charset="utf-8"><style>
body{margin:0;background:#0b0e11;color:#eaecef;font:13px -apple-system,Segoe UI,Roboto,sans-serif}
#bar{padding:8px 12px;color:#848e9c;display:flex;gap:16px;flex-wrap:wrap}
table{width:100%;border-collapse:collapse}th,td{padding:6px 10px;text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums}
th:nth-child(2),td:nth-child(2){text-align:left;font-weight:600}th{color:#848e9c;font-weight:500;border-bottom:1px solid #2b3139}
tr{border-bottom:1px solid #1e2329}.up{color:#0ecb81}.dn{color:#f6465d}.flashup{background:rgba(14,203,129,.35)}.flashdn{background:rgba(246,70,93,.35)}
td{transition:background .4s}.note{padding:8px 12px;color:#5e6673;font-size:12px;line-height:1.5}
</style></head><body><div id="bar"><span id="st">connecting…</span><span id="rate"></span><span id="age"></span></div>
<div style="overflow-x:auto"><table><thead><tr><th>#</th><th>Coin</th><th>Price</th><th>24h</th><th>1 min</th><th>Volume pace</th><th>Accel</th>
<th>Buyers (15s)</th><th>Net flow (15s)</th><th>Cooling 0-4</th><th>Status</th></tr></thead><tbody id="tb"></tbody></table></div>
<div class="note">Live from Binance futures, straight into this page. Volume pace = last 60 s traded vs this coin's average minute. Accel = last 30 s vs the 30 s before.
Buyers = share of the last 15 s of volume bought by aggressive buyers. Cooling counts: sellers &gt; buyers (double if buyers under 35%), net flow flipped negative, volume fading. 2+ = cooling off.
Numbers need about a minute per coin to fill in. If this says "disconnected", your internet or ISP is blocking Binance's live feed (try a VPN).</div>
<script>__CORE____GLUE__</script></body></html>"""


def build_html(top_n=10):
    return PAGE.replace("__CORE__", CORE.replace("__TOPN__", str(int(top_n)))).replace("__GLUE__", GLUE)


def render():
    import streamlit as st
    import streamlit.components.v1 as components
    st.subheader("📡 Live Tape")
    n = st.slider("Top risers to watch", 5, 20, 10, key="live_n")
    components.html(build_html(n), height=60 + 38 * n + 140, scrolling=False)

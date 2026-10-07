"""Daily Winners study (dashboard tab). Every UTC day: rank ALL liquid Binance USDT-futures coins by that day's gain, take the top N,
and measure what they did NEXT. Then look for the numbers (candle shape, volume surge, buyer share, run-up) that separate coins that kept
going from coins that faded, and test those rules on days the search never touched.

Honest rules: the ranking is only known at the day's close, so entry is the NEXT day's open (no peeking). Fees+slippage are charged once per
round trip. Funding paid/received on the next day's three 8-hour settlements is included (longs pay when it is positive, shorts receive it). The days are split 50% search / 25% validation / 25% FINAL; rules are searched on the first part, must also be
positive on the second, and only then are shown on the final part, which nothing was tuned on. The coin list is today's listed futures coins
(coins delisted since are missing -> small survivorship bias)."""
import itertools
import time

import numpy as np
import pandas as pd

import backtester as bt

FEATS = ["gain", "clv", "vol_x", "trades_x", "buy", "prev7", "btc"]
NAMES = {"gain": "Day gain %", "clv": "Close near day-high (0-1)", "vol_x": "Volume vs 20-day avg (x)", "trades_x": "Trade count vs 20-day avg (x)",
         "buy": "Buyer share of volume", "prev7": "Gain in the 7 days before %", "btc": "BTC gain that day %"}
GRID = dict(g_lo=[3, 6, 10, 15], g_hi=[25, 40, 1000], clv=[0, 0.5, 0.7, 0.85], vol_x=[0, 1.5, 3], buy=[0, 0.52, 0.55],
            prev7=[1000, 20, 5], rank=[3, 5, 10], btc=["any", "up"], side=[1, -1])


def funding(symbol, days):
    """Per-day sum of funding rates (in %) keyed by the UTC day-open time (ms). Cached for an hour. Empty Series if unavailable."""
    f = bt.CACHE / f"{symbol}_{days}_fund.pkl"
    if f.exists() and time.time() - f.stat().st_mtime < 3600:
        return pd.read_pickle(f)
    start, out = int((time.time() - days * 86400) * 1000), []
    while True:
        k = bt.api("/fapi/v1/fundingRate", {"symbol": symbol, "startTime": start, "limit": 1000})
        out += k
        if len(k) < 1000:
            break
        start = k[-1]["fundingTime"] + 1
        time.sleep(0.12)
    if not out:
        return pd.Series(dtype=float)
    d = pd.DataFrame(out)
    d["rate"] = d.fundingRate.astype(float) * 100
    day = (d.fundingTime.astype("int64") - 3_600_000) // 86_400_000 * 86_400_000  # settlements land a few ms after the hour; the 00:00 one belongs to the day that just ended
    ser = d.groupby(day).rate.sum()
    ser.to_pickle(f)
    return ser


def load(days, min_qv, progress=lambda x, m: None, use_funding=True):
    syms = bt.universe(700)
    data, fund = {}, {}
    for i, s in enumerate(syms):
        progress((i + 1) / len(syms), f"Loading daily candles: {s} ({i + 1}/{len(syms)})")
        cached = (bt.CACHE / f"{s}_{days + 40}_1d.pkl").exists()
        try:
            df = bt.history(s, days + 40, "1d")
        except Exception:
            continue
        if not cached:
            time.sleep(0.25)  # stay polite with Binance's rate limit
        if len(df) >= 40:
            data[s] = df
            if use_funding:
                try:
                    fund[s] = funding(s, days + 40)
                    time.sleep(0.15)
                except Exception:
                    pass  # no funding data for this coin -> treated as 0 (reported in the header)
    btc = bt.history("BTCUSDT", days + 40, "1d")
    if not data:
        raise RuntimeError("No candle data downloaded.")
    return build(data, btc, days, min_qv, fund)


def build(data, btc, days, min_qv, fund=None):
    """data: {symbol: daily dataframe}. Returns the event table: one row per (day, coin) that was in that day's top 20 gainers."""
    panel = pd.concat({s: d.set_index("t")[["o", "h", "l", "c", "qv", "n", "tbq"]] for s, d in data.items()}, axis=1)
    F = {k: panel.xs(k, axis=1, level=1) for k in ["o", "h", "l", "c", "qv", "n", "tbq"]}
    o, h, l, c, qv, n, tbq = (F[k] for k in ["o", "h", "l", "c", "qv", "n", "tbq"])
    gain = c / o - 1
    btc_g = pd.Series((btc.c / btc.o - 1).values, index=btc.t.values)
    feat = dict(
        gain=gain * 100,
        clv=(c - l) / (h - l).replace(0, np.nan),
        vol_x=qv / qv.rolling(20).mean().shift(1),
        trades_x=n / n.rolling(20).mean().shift(1),
        buy=tbq / qv.replace(0, np.nan),
        prev7=(c.shift(1) / c.shift(8) - 1) * 100,
    )
    r1 = c.shift(-1) / o.shift(-1) - 1  # next day open -> next day close
    r3 = c.shift(-3) / o.shift(-1) - 1  # hold 3 days from next open
    up1, dn1 = h.shift(-1) / o.shift(-1) - 1, l.shift(-1) / o.shift(-1) - 1
    elig = (qv >= min_qv) & gain.notna() & feat["clv"].notna()
    rank = gain.where(elig).rank(axis=1, ascending=False, method="first")
    mkt = r1.where(elig).mean(axis=1)  # the average liquid coin's next-day move: the benchmark
    mask = (rank <= 20).values
    idx = np.argwhere(mask)
    ev = pd.DataFrame({"t": c.index.values[idx[:, 0]], "sym": c.columns.values[idx[:, 1]]})
    for k, df in {**feat, "r1": r1 * 100, "r3": r3 * 100, "up1": up1 * 100, "dn1": dn1 * 100, "rank": rank}.items():
        ev[k] = df.values[idx[:, 0], idx[:, 1]]
    ev["mkt"] = ev.t.map(mkt * 100).values
    # funding over the NEXT day (the day the trade is held), in %; 0 when a coin has no funding data
    fd = pd.DataFrame({sym: ser for sym, ser in (fund or {}).items() if len(ser)}).reindex(c.index + 86_400_000) if fund else None
    if fd is not None and fd.shape[1]:
        fd.index = c.index
        ev["fund"] = fd.reindex(columns=c.columns).values[idx[:, 0], idx[:, 1]]
        ev["has_fund"] = ~np.isnan(ev["fund"].values)
        ev["fund"] = ev["fund"].fillna(0.0)
    else:
        ev["fund"], ev["has_fund"] = 0.0, False
    ev["btc"] = ev.t.map(btc_g * 100).values
    ev["nliq"] = ev.t.map(elig.sum(axis=1)).values
    ev = ev[ev.t >= c.index.max() - days * 86_400_000].dropna(subset=FEATS).reset_index(drop=True)
    return ev.sort_values(["t", "rank"]).reset_index(drop=True)


def split_days(ev):
    days = np.sort(ev.t.unique())
    a, b = days[int(len(days) * 0.5)], days[int(len(days) * 0.75)]
    return a, b


def stats(net, mkt=None):
    net = np.asarray(net, float)
    if len(net) == 0:
        return dict(n=0, avg=0.0, med=0.0, win=0.0, pf=0.0, tot=0.0, bench=0.0)
    w, ls = net[net > 0], net[net <= 0]
    return dict(n=len(net), avg=net.mean(), med=float(np.median(net)), win=(net > 0).mean() * 100,
                pf=(w.sum() / -ls.sum()) if ls.sum() < 0 else 99.0, tot=net.sum(), bench=float(np.mean(mkt)) if mkt is not None and len(mkt) else 0.0)


def day_t(t, net):
    """t-statistic of the average trade, clustered by day (coins on the same day move together, so they count as one observation)."""
    if len(net) < 2:
        return 0.0
    d = pd.Series(np.asarray(net, float)).groupby(np.asarray(t)).mean()
    if len(d) < 5 or d.std() == 0:
        return 0.0
    return float(d.mean() / (d.std() / np.sqrt(len(d))))


def rule_mask(ev, r, topn):
    m = (ev.gain >= r["g_lo"]) & (ev.gain <= r["g_hi"]) & (ev.clv >= r["clv"]) & (ev.vol_x >= r["vol_x"]) & (ev.buy >= r["buy"]) \
        & (ev.prev7 <= r["prev7"]) & (ev["rank"] <= min(r["rank"], topn))
    if r["btc"] == "up":
        m &= ev.btc > 0
    return m.values


def search(ev, topn, fee, min_ev):
    ev = ev[(ev["rank"] <= topn) & ev.r1.notna()].reset_index(drop=True)
    d1, d2 = split_days(ev)
    part = {"search": (ev.t < d1).values, "validate": ((ev.t >= d1) & (ev.t < d2)).values, "final": (ev.t >= d2).values}
    keys = list(GRID)
    found, seen = [], set()
    for combo in itertools.product(*[GRID[k] for k in keys]):
        r = dict(zip(keys, combo))
        m = rule_mask(ev, r, topn)
        sig = (r["side"], hash(m.tobytes()))
        if sig in seen:  # different settings that select exactly the same trades are one rule
            continue
        seen.add(sig)
        net = r["side"] * (ev.r1.values - ev.fund.values) - fee
        tr = stats(net[m & part["search"]])
        if tr["n"] < min_ev or tr["avg"] <= 0 or tr["pf"] < 1.1:
            continue
        va = stats(net[m & part["validate"]])
        if va["n"] < 20 or va["avg"] <= 0:
            continue
        found.append((tr["avg"] * np.sqrt(tr["n"]), r, tr, va))
    found.sort(key=lambda x: -x[0])
    best = []
    for _, r, tr, va in found[:5]:
        m = rule_mask(ev, r, topn)
        net = r["side"] * (ev.r1.values - ev.fund.values) - fee
        sel = m & part["final"]
        best.append(dict(r=r, train=tr, val=va, final=stats(net[sel]), final_t=day_t(ev.t.values[sel], net[sel]), curve=(ev.t.values[sel], net[sel])))
    base = {}
    for name, p in part.items():
        for side, lab in ((1, "Long"), (-1, "Short")):
            sel = p
            base[(lab, name)] = stats(side * (ev.r1.values[sel] - ev.fund.values[sel]) - fee, ev.mkt.values[sel] if side == 1 else None)
    return dict(best=best, base=base, tested=int(np.prod([len(v) for v in GRID.values()])), passed=len(found), d1=d1, d2=d2,
                lo=int(ev.t.min()), hi=int(ev.t.max()), ev=ev)


def buckets(ev, fee, upto):
    """Descriptive only, searched periods: next-day result of the top-N coins split by each number into 4 groups."""
    e = ev[ev.t < upto]
    rows = []
    for f in FEATS:
        try:
            q = pd.qcut(e[f], 4, duplicates="drop")
        except ValueError:
            continue
        for iv, g in e.groupby(q, observed=True):
            net = g.r1.values - g.fund.values - fee
            rows.append({"Number": NAMES[f], "Range": f"{iv.left:.2f} to {iv.right:.2f}", "Coins": len(g), "Avg next day %": round(net.mean(), 2),
                         "Win %": round((net > 0).mean() * 100, 1), "Fades (next day < -3%) %": round((g.r1 < -3).mean() * 100, 1)})
    return pd.DataFrame(rows)


def describe(r):
    s = f"{'LONG' if r['side'] == 1 else 'SHORT'} the day's top {r['rank']} when: gain {r['g_lo']}–{'∞' if r['g_hi'] >= 1000 else r['g_hi']}%"
    if r["clv"]:
        s += f", closed in the top {round((1 - r['clv']) * 100)}% of its day range"
    if r["vol_x"]:
        s += f", volume ≥ {r['vol_x']}× average"
    if r["buy"]:
        s += f", buyers ≥ {int(r['buy'] * 100)}% of volume"
    if r["prev7"] < 1000:
        s += f", gained ≤ {r['prev7']}% in the prior 7 days"
    if r["btc"] == "up":
        s += ", BTC up that day"
    return s + ". Enter next day open, exit that day's close."


def verdict(b):
    f = b["final"]
    if f["n"] < 20:
        return "⚠️ too few trades in the final period"
    if f["avg"] > 0 and f["pf"] > 1.15 and b.get("final_t", 0) >= 2.0:
        return "✅ still positive on the FINAL days nothing was tuned on, and statistically distinguishable from luck (day-level t ≥ 2)"
    if f["avg"] > 0 and f["pf"] > 1.15:
        return "⚠️ positive on the final days but NOT statistically convincing (day-level t < 2); this is what luck looks like when thousands of rules are tried"
    if f["avg"] > 0:
        return "⚠️ barely positive on the final days"
    return "❌ failed on the final days (it was luck / overfit)"


def render():
    import plotly.graph_objects as go
    import streamlit as st
    st.subheader("🏆 Daily Winners")
    st.caption("Every day: rank ALL liquid Binance futures coins by that day's gain, take the top N (a different group almost every day), and measure what they did "
               "the NEXT day. Then look for the numbers that separate coins that kept running from coins that faded. Entry = next day's open, so there is no peeking. "
               "Days are split 50% search / 25% validation / 25% FINAL; only rules positive in the first two are shown on the final part, which nothing was tuned on.")
    c = st.columns(5)
    days = c[0].slider("Days of history", 90, 540, 365, key="dw_days")
    topn = c[1].slider("Top N gainers each day", 3, 20, 10, key="dw_n")
    minqv = c[2].number_input("Min day volume (USDT millions)", 1.0, 500.0, 10.0, 1.0, key="dw_qv") * 1e6
    fee = c[3].number_input("Round-trip fee + slippage %", 0.05, 1.0, 0.20, 0.05, key="dw_fee")
    mine = c[4].slider("Min trades in search period", 30, 200, 60, 10, key="dw_min")
    if st.button("▶ Run daily winners study", type="primary", key="dw_run"):
        bar = st.progress(0.0, "Starting…")
        try:
            ev = load(days, minqv, lambda x, m: bar.progress(min(x, 1.0) * 0.8, m))
            bar.progress(0.85, "Searching rules…")
            res = search(ev, topn, fee, mine)
            res.update(bk=buckets(res["ev"], fee, res["d2"]), topn=topn, fee=fee, days=days)
            st.session_state["dw_res"] = res
            bar.empty()
        except Exception as ex:
            bar.empty()
            st.error(f"Could not finish: {ex}")
    res = st.session_state.get("dw_res")
    if not res:
        st.info("Press Run. The first run downloads about 300 coins of daily candles (1–3 minutes); later runs reuse the cache for an hour.")
        return
    ev, fee, topn = res["ev"], res["fee"], res["topn"]
    ms = lambda t: pd.to_datetime(t, unit="ms").strftime("%Y-%m-%d")
    st.markdown(f"**Search:** {ms(res['lo'])} → {ms(res['d1'])}  |  **Validation:** {ms(res['d1'])} → {ms(res['d2'])}  |  **FINAL:** {ms(res['d2'])} → {ms(res['hi'])}  |  "
                f"funding data on {ev.has_fund.mean() * 100:.0f}% of trades  |  {ev.t.nunique()} days, {int(ev.nliq.median())} liquid coins per day, {ev.sym.nunique()} different coins appeared in the top {topn}")

    st.markdown(f"### 1. What happens to the day's top {topn} the next day (no rules, just everyone)")
    rows = {}
    for (lab, per), s in res["base"].items():
        rows[f"{lab} the top {topn} – {per}"] = {"Trades": s["n"], "Avg next-day %": round(s["avg"], 2), "Median %": round(s["med"], 2), "Win %": round(s["win"], 1),
                                                 "Profit factor": round(s["pf"], 2), "Avg coin that day %": round(s["bench"], 2) if lab == "Long" else None}
    st.dataframe(pd.DataFrame(rows).T, use_container_width=True)
    st.caption("Net of fees. 'Avg coin that day' = what the average liquid coin did the same next day (market drift), the bar a long must beat. "
               "Funding is included (real settlements from Binance; coins without funding data count as 0). No stop is simulated, so squeeze risk on shorts is not shown.")

    st.markdown("### 2. Which numbers separate the continuers from the fades (search + validation days only)")
    st.dataframe(res["bk"], use_container_width=True, hide_index=True)

    st.markdown("### 3. Rules found — judged on the FINAL days")
    if not res["best"]:
        st.warning("No rule was positive in BOTH the search and validation periods with enough trades. That is a real answer: the day's top gainers "
                   "show no reliable, rule-based edge after fees in this data. Try more days or a different Top N.")
    else:
        out = {}
        for i, b in enumerate(res["best"]):
            for lab, k in (("search", "train"), ("validation", "val"), ("FINAL", "final")):
                s = b[k]
                out[f"Rule #{i + 1} – {lab}"] = {"Trades": s["n"], "Avg %": round(s["avg"], 2), "Win %": round(s["win"], 1), "Profit factor": round(s["pf"], 2),
                                                  "Total %": round(s["tot"], 1), "Day-level t": round(b["final_t"], 2) if k == "final" else None}
        st.dataframe(pd.DataFrame(out).T, use_container_width=True)
        best = res["best"][0]
        st.markdown(f"**Best rule: {verdict(best)}**")
        st.code(describe(best["r"]), language="text")
        t, net = best["curve"]
        if len(t):
            fig = go.Figure(go.Scatter(x=pd.to_datetime(t, unit="ms"), y=np.cumsum(net), mode="lines+markers", name="cumulative %"))
            fig.update_layout(title="Best rule: cumulative % (sum of trade returns) on the FINAL days", height=320, margin=dict(l=10, r=10, t=40, b=10))
            st.plotly_chart(fig, use_container_width=True)
        last = ev[ev.t == ev.t.max()]
        last = last[last["rank"] <= topn]
        r = best["r"]
        ok = rule_mask(last.reset_index(drop=True), r, topn)
        view = last.reset_index(drop=True)
        view["Passes best rule"] = np.where(ok, "✅", "")
        st.markdown(f"### 4. Latest completed day ({ms(ev.t.max())}): top {topn} gainers and which pass the best rule")
        st.dataframe(view[["rank", "sym", "gain", "clv", "vol_x", "buy", "prev7", "Passes best rule"]].round(2).rename(columns={
            "rank": "Rank", "sym": "Coin", "gain": "Day gain %", "clv": "Close near high", "vol_x": "Volume x", "buy": "Buyer share", "prev7": "Prior 7d %"}),
            use_container_width=True, hide_index=True)
        if not (best["final"]["n"] >= 20 and best["final"]["avg"] > 0):
            st.caption("The best rule did not hold on the final days, so treat this list as information, not a signal.")
    with st.expander("Last 30 days: the top gainers and what they did next"):
        e = ev[(ev["rank"] <= topn)].copy()
        e = e[e.t >= e.t.max() - 30 * 86_400_000]
        e["Day"] = pd.to_datetime(e.t, unit="ms").dt.strftime("%m-%d")
        st.dataframe(e[["Day", "rank", "sym", "gain", "clv", "vol_x", "r1", "r3", "up1", "dn1"]].round(2).rename(columns={
            "rank": "Rank", "sym": "Coin", "gain": "Day gain %", "clv": "Close near high", "vol_x": "Volume x", "r1": "Next day %", "r3": "3 days %",
            "up1": "Next-day high %", "dn1": "Next-day low %"}).sort_values(["Day", "Rank"], ascending=[False, True]), use_container_width=True, hide_index=True)

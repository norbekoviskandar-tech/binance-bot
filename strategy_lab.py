"""Strategy Lab (dashboard tab). Takes the simple 'volume surge + RSI' long strategy, fixes its simulation bugs,
and searches for better settings WITHOUT fooling itself: parameters are picked on the older 60% of the data and
judged only on the newer 40% that the search never saw.

Simulation rules: signal on a closed candle -> entry at the NEXT candle open; stop is checked BEFORE the target inside
a candle (pessimistic); gaps fill at the open; fees+slippage charged once per round trip; one position per coin at a time.
Results are reported in R (1R = the distance to the stop) so they do not depend on account size."""
import itertools
import random

import numpy as np
import pandas as pd

import backtester as bt

# The strategy exactly as originally written (fixed % stop/target, no trend filter), with the bugs removed.
ORIGINAL = dict(vol_x=1.5, rsi_lo=45, rsi_hi=70, trend="off", breakout=0, green=False,
                stop_pct=2.0, stop_atr=0.0, tp_r=2.5, be=False, trail_atr=0.0, max_hold=0)

GRID = dict(
    vol_x=[1.5, 2.0, 3.0],
    rsi=[(45, 70), (50, 70), (55, 75)],
    trend=["off", "ema50", "stack"],  # off / close>EMA50 / close>EMA50>EMA200
    breakout=[0, 20],  # 0 = off, else close must break the highest high of the last N candles
    green=[False, True],
    stop_atr=[1.5, 2.0, 3.0],
    tp_r=[1.5, 2.0, 3.0],
    be=[False, True],
    trail_atr=[0.0, 2.5],
    max_hold=[24, 48, 96],
)


def features(df):
    c, h, l, v = df.c, df.h, df.l, df.v
    d = c.diff()
    up = d.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()  # Wilder RSI, aligned to the candle (the old one was shifted by 1)
    dn = (-d.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    rsi = 100 - 100 / (1 + up / dn.replace(0, np.nan))
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    f = pd.DataFrame({
        "rsi": rsi,
        "vol_x": v / v.rolling(20).mean().shift(1),  # compared with the PREVIOUS 20 candles, not including itself
        "atr": tr.ewm(alpha=1 / 14, adjust=False).mean(),
        "ema50": c.ewm(span=50, adjust=False).mean(),
        "ema200": c.ewm(span=200, adjust=False).mean(),
        "green": c > df.o,
    })
    for n in (20,):
        f[f"hh{n}"] = h.rolling(n).max().shift(1)
    return f


def signal_idx(df, f, p):
    m = (f.vol_x >= p["vol_x"]) & (f.rsi > p["rsi_lo"]) & (f.rsi < p["rsi_hi"])
    if p["trend"] in ("ema50", "stack"):
        m &= df.c > f.ema50
    if p["trend"] == "stack":
        m &= f.ema50 > f.ema200
    if p["breakout"]:
        m &= df.c > f.hh20
    if p["green"]:
        m &= f.green
    if p.get("top_n"):
        m &= f.rank24 <= p["top_n"]  # coin must be among the day's top-N risers (24h change) at the signal candle
    m &= f.ema200.notna() & f.atr.notna()
    return np.flatnonzero(m.fillna(False).values)


def simulate(o, h, l, c, atr, t, i0, p, fee):
    """One long trade from the signal on candle i0. Returns (trade dict, exit index) or None."""
    n = len(o)
    e = i0 + 1
    if e >= n:
        return None
    entry = o[e]
    risk = entry * p["stop_pct"] / 100 if p["stop_pct"] else p["stop_atr"] * atr[i0]
    if not (risk > 0) or risk / entry > 0.15:
        return None
    stop, tp, armed = entry - risk, entry + p["tp_r"] * risk, False
    last = min(n - 1, e + p["max_hold"] - 1) if p["max_hold"] else n - 1
    timed = bool(p["max_hold"]) and e + p["max_hold"] - 1 <= n - 1
    px, why, j = c[last], "time" if timed else "end", last
    for k in range(e, last + 1):
        if l[k] <= stop:  # stop first (pessimistic); a gap below the stop fills at the open
            px, why, j = min(o[k], stop), "SL" if not armed else "BE/trail", k
            break
        if h[k] >= tp:
            px, why, j = max(o[k], tp), "TP", k
            break
        if not armed and h[k] >= entry + risk:  # reached +1R: protect the trade from the next candle on
            armed = True
            if p["be"]:
                stop = max(stop, entry)
        if armed and p["trail_atr"]:
            stop = max(stop, h[k] - p["trail_atr"] * atr[k])
    ret = (px / entry - 1) * 100 - fee
    return dict(t_in=int(t[e]), t_out=int(t[j]), R=ret / (risk / entry * 100), ret=ret, why=why, bars=j - e + 1), j


def run_config(data, p, fee, t0=None, t1=None):
    """data: {symbol: (df, f, arrays)}. Trades from candles whose timestamp is in [t0, t1)."""
    out = []
    for s, (df, f, a) in data.items():
        busy = -1
        for i in signal_idx(df, f, p):
            if i <= busy:
                continue
            ts = a["t"][i]
            if (t0 is not None and ts < t0) or (t1 is not None and ts >= t1):
                continue
            r = simulate(a["o"], a["h"], a["l"], a["c"], a["atr"], a["t"], i, p, fee)
            if r:
                tr, busy = r
                tr["sym"] = s
                out.append(tr)
    out.sort(key=lambda x: x["t_in"])
    return out


def metrics(tr):
    if not tr:
        return dict(trades=0, win=0.0, exp=0.0, pf=0.0, total_R=0.0, dd=0.0, avg_win=0.0, avg_loss=0.0, avg_ret=0.0)
    R = np.array([t["R"] for t in tr])
    w, ls = R[R > 0], R[R <= 0]
    eq = np.cumsum(R)
    return dict(trades=len(R), win=(R > 0).mean() * 100, avg_win=w.mean() if len(w) else 0.0, avg_loss=ls.mean() if len(ls) else 0.0,
                exp=R.mean(), total_R=R.sum(), pf=(w.sum() / -ls.sum()) if ls.sum() < 0 else float("inf"),
                dd=(np.maximum.accumulate(eq) - eq).max(), avg_ret=np.mean([t["ret"] for t in tr]))


def load(symbols, days, progress=lambda x, m: None, interval="1h"):
    data = {}
    for i, s in enumerate(symbols):
        progress((i + 1) / len(symbols), f"Loading {interval} candles: {s} ({i + 1}/{len(symbols)})")
        df = bt.history(s, days, interval)
        if len(df) < 400:
            continue
        f = features(df)
        data[s] = (df, f, dict(o=df.o.values, h=df.h.values, l=df.l.values, c=df.c.values, atr=f.atr.values, t=df.t.values))
    if not data:
        raise RuntimeError("No candle data downloaded.")
    bars24 = 24 if interval == "1h" else 96
    ret = pd.DataFrame({s: pd.Series((df.c / df.c.shift(bars24) - 1).values, index=df.t.values) for s, (df, f, a) in data.items()})
    rk = ret.rank(axis=1, ascending=False)  # 1 = biggest 24h gainer among the loaded coins at that moment
    for s, (df, f, a) in data.items():
        f["rank24"] = df.t.map(rk[s]).values
    return data


def sample_configs(n, seed=7):
    keys = list(GRID)
    space = [GRID[k] for k in keys]
    total = int(np.prod([len(x) for x in space]))
    rng = random.Random(seed)
    picks = set(rng.sample(range(total), min(n, total)))
    cfgs = []
    for idx, combo in enumerate(itertools.product(*space)):
        if idx in picks:
            d = dict(zip(keys, combo))
            d["rsi_lo"], d["rsi_hi"] = d.pop("rsi")
            d["stop_pct"] = 0.0
            cfgs.append(d)
    return cfgs


def optimize(data, fee, n_cfg=250, split=0.6, min_trades=40, progress=lambda x, m: None, top_n=0):
    ts = np.concatenate([a["t"] for _, _, a in data.values()])
    lo, hi = int(ts.min()), int(ts.max())
    cut = int(lo + (hi - lo) * split)
    rows = []
    cfgs = [dict(p, top_n=top_n) for p in sample_configs(n_cfg)]
    orig = dict(ORIGINAL, top_n=top_n)
    for i, p in enumerate(cfgs):
        if i % 10 == 0:
            progress((i + 1) / len(cfgs), f"Testing settings {i + 1}/{len(cfgs)}")
        m = metrics(run_config(data, p, fee, lo, cut))
        if m["trades"] >= min_trades and m["exp"] > 0 and m["pf"] > 1.1:
            rows.append((m["exp"] * np.sqrt(m["trades"]), p, m))  # reward edge, but only if it is backed by enough trades
    rows.sort(key=lambda x: -x[0])
    best = []
    for _, p, mtr in rows[:5]:
        best.append(dict(p=p, train=mtr, test=metrics(run_config(data, p, fee, cut, hi + 1))))
    base = dict(train=metrics(run_config(data, orig, fee, lo, cut)), test=metrics(run_config(data, orig, fee, cut, hi + 1)), p=orig)
    return dict(best=best, base=base, cut=cut, lo=lo, hi=hi, tested=len(cfgs), passed=len(rows))


def verdict(b):
    tr, te = b["train"], b["test"]
    if te["trades"] < 20:
        return "⚠️ too few trades in the unseen data"
    if te["exp"] > 0 and te["pf"] > 1.15:
        return "✅ still profitable on data the search never saw"
    if te["exp"] > 0:
        return "⚠️ barely positive on unseen data"
    return "❌ fell apart on unseen data (it was luck / overfit)"


def render():
    import plotly.graph_objects as go
    import streamlit as st
    st.subheader("🧬 Strategy Lab")
    st.caption("Fixes the simulation bugs in the original script and searches for better settings. Settings are chosen on the OLDER 60% of "
               "history and judged on the NEWER 40% they never saw — if a setting only looks good in the first part, it is rejected. "
               "Real Binance futures candles, public data. Past results do not guarantee future results.")
    c = st.columns(5)
    days = c[0].slider("Days of history", 90, 365, 180, key="sl_days")
    nsym = c[1].slider("Coin pool (top by volume)", 3, 60, 40, key="sl_n")
    fee = c[2].number_input("Round-trip fee + slippage %", 0.05, 1.0, 0.20, 0.05, key="sl_fee")
    ncfg = c[3].slider("Settings to try", 50, 600, 250, 50, key="sl_cfg")
    mint = c[4].slider("Min trades to count", 20, 100, 40, 5, key="sl_min")
    d = st.columns([2, 3])
    mode = d[0].selectbox("Which coins may be traded", ["Day's top 10 gainers", "Day's top 5 gainers", "Day's top 20 gainers", "All loaded coins"], key="sl_mode",
                          help="Top gainers = the coin must be among the biggest 24h risers (out of the loaded coins) at the moment of the signal. "
                               "It changes every day, unlike 'top by volume'. Load more coins so the ranking is meaningful.")
    top_n = {"Day's top 10 gainers": 10, "Day's top 5 gainers": 5, "Day's top 20 gainers": 20}.get(mode, 0)
    d[1].caption("**Coins slider** = how many of the biggest Binance futures coins are loaded as the pool. The day's top gainers are ranked "
                 "inside that pool, so use 40–60 coins for the top-10 modes (with only 10 coins, 'top 10' would mean everything).")
    if st.button("▶ Run strategy lab", type="primary", key="sl_run"):
        bar = st.progress(0.0, "Starting…")
        try:
            syms = ["BTCUSDT"] + bt.universe(nsym)[:max(0, nsym - 1)]
            data = load(syms, days, lambda x, m: bar.progress(min(x, 1.0) * 0.5, m))
            res = optimize(data, fee, ncfg, 0.6, mint, lambda x, m: bar.progress(0.5 + min(x, 1.0) * 0.5, m), top_n)
            res["mode"], res["ncoins"] = mode, len(data)
            res["curves"] = {"base": run_config(data, res["base"]["p"], fee, res["cut"], res["hi"] + 1),
                             **{i: run_config(data, b["p"], fee, res["cut"], res["hi"] + 1) for i, b in enumerate(res["best"])}}
            st.session_state["sl_res"] = res
            bar.empty()
        except Exception as ex:
            bar.empty()
            st.error(f"Could not finish: {ex}")
    res = st.session_state.get("sl_res")
    if not res:
        st.info("Press Run. First run downloads candles (about a minute); later runs reuse the cache for an hour.")
        return
    fmt = lambda t: dict(Trades=t["trades"], **{"Win %": round(t["win"], 1), "Avg win R": round(t["avg_win"], 2), "Avg loss R": round(t["avg_loss"], 2),
                                                "Expectancy R": round(t["exp"], 3), "Profit factor": round(min(t["pf"], 99), 2),
                                                "Total R": round(t["total_R"], 1), "Max DD R": round(t["dd"], 1)})
    ms = lambda ts: pd.to_datetime(ts, unit="ms").strftime("%Y-%m-%d")
    st.markdown(f"**Search period:** {ms(res['lo'])} → {ms(res['cut'])}   |   **Unseen test period:** {ms(res['cut'])} → {ms(res['hi'])}   |   "
                f"{res['tested']} settings tried, {res['passed']} passed the training filter  \n"
                f"**Coins:** {res.get('mode', '')} (pool of {res.get('ncoins', '?')} coins)")
    rows = {"Original script (bugs fixed) – search period": fmt(res["base"]["train"]), "Original script (bugs fixed) – UNSEEN": fmt(res["base"]["test"])}
    for i, b in enumerate(res["best"]):
        rows[f"Candidate #{i + 1} – search period"] = fmt(b["train"])
        rows[f"Candidate #{i + 1} – UNSEEN"] = fmt(b["test"])
    st.dataframe(pd.DataFrame(rows).T, use_container_width=True)
    if not res["best"]:
        st.warning("No setting beat the training filter (positive expectancy, profit factor > 1.1, enough trades). That is a real answer: "
                   "this idea has no reliable edge after fees on these coins. Try more days or more coins, or a different idea.")
        return
    best = res["best"][0]
    st.markdown(f"### Best candidate: {verdict(best)}")
    p = best["p"]
    st.code(f"volume ≥ {p['vol_x']}× average | RSI {p['rsi_lo']}–{p['rsi_hi']} | trend filter: {p['trend']} | breakout of last {p['breakout'] or '–'} candles | "
            f"green candle: {p['green']}\nstop {p['stop_atr']}×ATR | take profit {p['tp_r']}R | breakeven at +1R: {p['be']} | "
            f"trail {p['trail_atr'] or 'off'}×ATR | max hold {p['max_hold']} candles", language="text")
    fig = go.Figure()
    for k, name in [("base", "Original"), (0, "Best candidate")]:
        tr = res["curves"].get(k, [])
        if tr:
            fig.add_trace(go.Scatter(x=pd.to_datetime([t["t_out"] for t in tr], unit="ms"), y=np.cumsum([t["R"] for t in tr]), mode="lines", name=name))
    fig.update_layout(title="Cumulative R on the UNSEEN period (after fees)", height=340, margin=dict(l=10, r=10, t=40, b=10))
    st.plotly_chart(fig, use_container_width=True)
    tr = res["curves"].get(0, [])
    if tr:
        why = pd.Series([t["why"] for t in tr]).value_counts()
        st.caption("How the best candidate's unseen trades ended: " + ", ".join(f"{k} {v}" for k, v in why.items()))
    with st.expander("What was wrong with the original script"):
        st.markdown(
            "- **RSI was shifted by one candle** (`np.diff` drops a value, so every RSI belonged to the previous candle) and used a plain average instead of Wilder smoothing.\n"
            "- **No fees or slippage** — with a 2% stop and 5% target, 0.2% of costs matters a lot.\n"
            "- **Exit only checked on the candle close**, so intra-candle stop and target hits were missed or filled at the wrong price.\n"
            "- **Entered at the signal candle's close**, which cannot be traded; this version enters at the next open.\n"
            "- **Candle download repeated the last candle** and `BTCUSDT` is not a valid ccxt symbol (`BTC/USDT`).\n"
            "- **Unused risk settings** (`ACCOUNT_SIZE`, `MAX_LOSS_PER_TRADE`) and a profit factor computed from averages instead of totals.\n"
            "- **One coin, one period, nothing held back** — any tuning on that is just curve-fitting. This lab keeps a part of the data hidden.")

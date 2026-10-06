"""Strategy backtester (dashboard tab). Real Binance USD-M futures 15m candles, public data, no API key.
Rules of the simulation: entry at the NEXT candle open; stop is checked BEFORE targets inside a candle (pessimistic);
fees+slippage charged once per trade; one position at a time; daily trade cap and 'stop after N losses in a row'."""
import time
from pathlib import Path

import numpy as np
import pandas as pd
import requests

import gainers_study as gs
import velocity as vel

BASE = "https://fapi.binance.com"
STABLE = {"USDC", "FDUSD", "TUSD", "BUSD", "USDP", "DAI", "USDE"}
CACHE = Path("bt_cache")
CACHE.mkdir(exist_ok=True)
BAR_MS = 900_000

COMMON = dict(entry="signal", ret1h_lo=1.5, ret1h_hi=8.0, use_volume=True, vol_x=3.0, use_buyers=True, buy_ratio=0.57,
              use_trades=True, trades_x=2.5, use_breakout=True, ext_atr=3.0, btc_filter=False,
              pb_atr=0.5, arm_bars=8, pb_buy=0.55, stop_atr=2.0, tp1_r=2.2, tp1_frac=0.5, tp2_r=3.5,
              be_after_tp1=False, trail_bars=0, time_bars=0, time_min_r=0.5, max_hold=96,
              cooldown=16, max_per_day=3, stop_after_losses=3,
              tf="15m", top_n=0, min_vol24=0, accel_min=1.0, min_stop_pct=0.0, fade_frac=0.0)

FAST = "G. Top-10 volume velocity (fast in/out)"
FAST_P = dict(tf="1m", top_n=10, min_vol24=2e7, ret1h_lo=0.8, ret1h_hi=6.0, vol_x=4.0, accel_min=1.3, buy_ratio=0.58, trades_x=2.5,
              ext_atr=10.0, btc_filter=True, stop_atr=2.0, min_stop_pct=0.6, tp1_r=1.0, tp1_frac=0.5, tp2_r=2.5, be_after_tp1=True,
              trail_bars=5, time_bars=20, time_min_r=0.4, fade_frac=0.4, max_hold=90, cooldown=15, max_per_day=6)

PRESETS = {
    "A. Breakout chase (bot's current exits)": dict(),
    "B. Plain gainers (no filters)": dict(use_volume=False, use_buyers=False, use_trades=False, use_breakout=False),
    "C. Pullback + buyers + BTC filter (recommended)": dict(entry="pullback", ret1h_hi=5.0, btc_filter=True, stop_atr=1.8, tp1_r=1.0,
                                                           tp1_frac=0.5, tp2_r=3.0, be_after_tp1=True, trail_bars=4, time_bars=8),
    "D. Scalp (tight stop, quick exit)": dict(btc_filter=True, stop_atr=0.8, tp1_r=1.2, tp1_frac=1.0, tp2_r=1.2, time_bars=4),
    FAST: FAST_P,
    "H. Fast scalp (same signal, quick exit)": {**FAST_P, "tp1_r": 1.2, "tp1_frac": 1.0, "tp2_r": 1.2, "min_stop_pct": 0.5, "time_bars": 10,
                                               "fade_frac": 0.5, "trail_bars": 0, "be_after_tp1": False},
    "E. Strict breakout + BTC filter": dict(vol_x=4.0, buy_ratio=0.60, ret1h_hi=6.0, btc_filter=True, stop_atr=1.8, tp1_r=1.5,
                                           tp1_frac=0.5, tp2_r=3.0, be_after_tp1=True, trail_bars=4, time_bars=8),
}


def api(path, params=None):
    for a in range(3):
        r = requests.get(BASE + path, params=params, timeout=20)
        if r.status_code in (418, 429):
            time.sleep(5 * (a + 1))
            continue
        r.raise_for_status()
        return r.json()
    raise RuntimeError("Binance is rate-limiting this server. Wait a minute and run again.")


def universe(n):
    ok = {s["symbol"] for s in api("/fapi/v1/exchangeInfo")["symbols"] if s["status"] == "TRADING"
          and s.get("contractType") == "PERPETUAL" and s["quoteAsset"] == "USDT" and s["baseAsset"] not in STABLE}
    rows = sorted((x for x in api("/fapi/v1/ticker/24hr") if x["symbol"] in ok), key=lambda x: -float(x["quoteVolume"]))
    return [x["symbol"] for x in rows if x["symbol"] != "BTCUSDT"][:n]


def frame(raw):
    df = pd.DataFrame(raw, columns=["t", "o", "h", "l", "c", "v", "ct", "qv", "n", "tbb", "tbq", "_"])
    for c in ["o", "h", "l", "c", "v", "qv", "n", "tbb", "tbq"]:
        df[c] = df[c].astype(float)
    return df.iloc[:-1].reset_index(drop=True)  # drop the still-open candle


def history(symbol, days, interval="15m"):
    f = CACHE / f"{symbol}_{days}_{interval}.pkl"
    if f.exists() and time.time() - f.stat().st_mtime < 3600:
        return pd.read_pickle(f)
    start, out = int((time.time() - days * 86400) * 1000), []
    while True:
        k = api("/fapi/v1/klines", {"symbol": symbol, "interval": interval, "startTime": start, "limit": 1500})
        out += k
        if len(k) < 1500:
            break
        start = k[-1][0] + 1
        time.sleep(0.12)
    df = frame(out)
    df.to_pickle(f)
    return df


def prep(df, btc_ok, tf="15m"):
    if tf == "1m":
        f = vel.features_1m(df)
    else:
        f = gs.features(df)
        tr = pd.concat([df.h - df.l, (df.h - df.c.shift()).abs(), (df.l - df.c.shift()).abs()], axis=1).max(axis=1)
        f["atr"] = tr.rolling(14).mean()
        f["level"] = df.h.rolling(32).max().shift(1)  # the breakout level
        f["bar_buy"] = df.tbq / df.qv.replace(0, np.nan)  # buyer share inside a single candle
    f["btc_ok"] = df.t.map(btc_ok).fillna(False).astype(bool)
    return f


def setup_mask(f, p):
    m = f.ret1h.between(p["ret1h_lo"], p["ret1h_hi"]) & (f.ext_atr <= p["ext_atr"])
    if p["use_volume"]:
        m &= (f.vol_x >= p["vol_x"]) & (f.vol_accel >= p["accel_min"])
    if p["use_buyers"]:
        m &= f.buy_ratio >= p["buy_ratio"]
    if p["use_trades"]:
        m &= f.trades_x >= p["trades_x"]
    if p["use_breakout"]:
        m &= f.breakout.astype(bool)
    if p["btc_filter"]:
        m &= f.btc_ok
    if p["top_n"]:
        m &= f.rank24 <= p["top_n"]  # must be one of today's top-N risers (ranked among the loaded coins)
    if p["min_vol24"]:
        m &= f.vol24 >= p["min_vol24"]
    return m.fillna(False)


def entries(df, f, p):
    m, n = setup_mask(f, p).values, len(df)
    o, l, c = df.o.values, df.l.values, df.c.values
    atr, lvl, bb = f.atr.values, f.level.values, f.bar_buy.values
    out, last = [], -10**9
    for i in np.flatnonzero(m):
        if i - last < p["cooldown"] or i + 2 >= n or np.isnan(atr[i]):
            continue
        if p["entry"] == "signal":
            out.append(i + 1)
            last = i
            continue
        for j in range(i + 1, min(i + 1 + p["arm_bars"], n - 1)):  # wait for a pullback to the breakout level
            if c[j] < lvl[i] - 0.5 * atr[j]:
                break  # breakout failed
            if l[j] <= lvl[i] + p["pb_atr"] * atr[j] and c[j] > lvl[i] and c[j] > o[j] and bb[j] >= p["pb_buy"]:
                out.append(j + 1)
                last = j
                break
    return out


def simulate(df, f, e, p, fee_pct):
    o, h, l, c, t = df.o.values, df.h.values, df.l.values, df.c.values, df.t.values
    price, atr0 = o[e], f.atr.values[e - 1]
    R = max(p["stop_atr"] * atr0, p["min_stop_pct"] / 100 * price)
    if not R > 0 or R / price * 100 < 0.1:
        return None
    stop, frac, real, tp1, best, done, reason = price - R, 1.0, 0.0, False, price, False, "max hold"
    end = min(e + p["max_hold"], len(df)) - 1
    bar_ms = int(t[1] - t[0])
    v3 = pd.Series(df.qv.values).rolling(3).sum().values if p["fade_frac"] else None
    peak = float(v3[e - 1]) if v3 is not None and v3[e - 1] == v3[e - 1] else 0.0
    k = e
    for k in range(e, end + 1):
        if l[k] <= stop:
            real += frac * (stop - price) / R
            reason, done = ("stop" if not tp1 else "breakeven/trail"), True
            break
        best = max(best, h[k])
        if not tp1 and h[k] >= price + p["tp1_r"] * R:
            real += p["tp1_frac"] * p["tp1_r"]
            frac -= p["tp1_frac"]
            tp1 = True
            if p["be_after_tp1"]:
                stop = max(stop, price)
            if frac <= 1e-9:
                reason, done = "target", True
                break
        if tp1 and h[k] >= price + p["tp2_r"] * R:
            real += frac * p["tp2_r"]
            reason, done = "target", True
            break
        if tp1 and p["trail_bars"] > 0 and k >= e + p["trail_bars"] - 1:
            stop = max(stop, l[k - p["trail_bars"] + 1:k + 1].min())
        if v3 is not None:
            peak = max(peak, v3[k])
            if k - e >= 3 and v3[k] < p["fade_frac"] * peak:  # volume velocity died: get out
                real += frac * (c[k] - price) / R
                reason, done = "volume faded", True
                break
        if p["time_bars"] and k - e + 1 >= p["time_bars"] and not tp1 and (best - price) / R < p["time_min_r"]:
            real += frac * (c[k] - price) / R
            reason, done = "time stop", True
            break
    if not done:
        real += frac * (c[k] - price) / R
    net = real - fee_pct / 100 * price / R
    return dict(sym="", t_in=int(t[e]), t_out=int(t[k]) + bar_ms, R=net, reason=reason, ret_pct=net * R / price * 100,
                entry=price, score=float(f.vol_x.values[e - 1]))


def select(trades, p):
    trades.sort(key=lambda x: (x["t_in"], -x["score"]))
    out, busy, day, cnt, losses = [], 0, None, 0, 0
    for t in trades:
        if t["t_in"] < busy:
            continue
        d = t["t_in"] // 86_400_000
        if d != day:
            day, cnt, losses = d, 0, 0
        if cnt >= p["max_per_day"] or losses >= p["stop_after_losses"]:
            continue
        out.append(t)
        busy, cnt = t["t_out"], cnt + 1
        losses = losses + 1 if t["R"] < 0 else 0
    return out


def metrics(tr, risk, days):
    if not tr:
        return dict(trades=0)
    R = np.array([t["R"] for t in tr])
    w, ls = R[R > 0], R[R <= 0]
    eq = np.cumsum(R)
    return dict(trades=len(R), per_day=len(R) / days, win=(R > 0).mean() * 100, avg_win=w.mean() if len(w) else 0.0,
                avg_loss=ls.mean() if len(ls) else 0.0, exp=R.mean(), total_R=R.sum(),
                pf=(w.sum() / -ls.sum()) if ls.sum() < 0 else float("inf"),
                dd=(np.maximum.accumulate(eq) - eq).max(), pnl=R.sum() * risk)


def run(params, days, nsym, fee, progress=lambda x, m: None, days_1m=7):
    syms = universe(nsym)
    store, span = {}, {}
    tfs = sorted({p["tf"] for p in params.values()})
    for ti, tf in enumerate(tfs):
        d = days if tf == "15m" else days_1m + 1  # +1 day warm-up for the 24h ranking
        bph = 4 if tf == "15m" else 60
        btc = history("BTCUSDT", d, tf)
        ok = (btc.c > btc.c.ewm(span=20 * bph, adjust=False).mean()) & (btc.c / btc.c.shift(bph) > 1)  # BTC above 1h EMA20, up on the hour
        btc_ok = pd.Series(ok.values, index=btc.t.values)
        dd = {}
        for i, s in enumerate(syms):
            progress((ti + (i + 1) / (len(syms) + 1)) / len(tfs), f"Loading {tf} candles: {s} ({i + 1}/{len(syms)})")
            df = history(s, d, tf)
            if len(df) > (400 if tf == "15m" else 1700):
                dd[s] = (df, prep(df, btc_ok, tf))
        if not dd:
            raise RuntimeError("No candle data downloaded.")
        if tf == "1m":  # rank every coin by 24h change at every minute -> 'top risers right now'
            rk = pd.DataFrame({s: pd.Series(f.ret24.values, index=df.t.values) for s, (df, f) in dd.items()}).rank(axis=1, ascending=False)
            for s, (df, f) in dd.items():
                f["rank24"] = df.t.map(rk[s]).values
        store[tf] = dd
        warm = 1440 * 60_000 if tf == "1m" else 0
        span[tf] = (int(min(df.t.iloc[0] for df, _ in dd.values())) + warm, int(max(df.t.iloc[-1] for df, _ in dd.values())),
                    days if tf == "15m" else days_1m)
    res, info = {}, {}
    for name, p in params.items():
        trades = []
        for s, (df, f) in store[p["tf"]].items():
            for e in entries(df, f, p):
                t = simulate(df, f, e, p, fee)
                if t:
                    t["sym"] = s
                    trades.append(t)
        res[name] = select(trades, p)
        t0, t1, dy = span[p["tf"]]
        info[name] = dict(t0=t0, t1=t1, days=dy)
    return res, info


def verdict(m, mo, mn):
    if m["trades"] < 30:
        return "⚠️ too few trades"
    if m["exp"] > 0 and mn.get("exp", -1) > 0 and mo.get("exp", -1) > 0:
        return "✅ positive in older AND newer data"
    return "❌ not reliable" if m["exp"] <= 0 else "⚠️ positive but not in both halves"


def render(risk_usd=0.3, equity=30.0):
    import streamlit as st
    st.subheader("🧪 Strategy Backtester")
    st.caption("Replays strategies on real Binance futures 15m candles (public data). Entry at next candle open, fees+slippage charged, "
               "stop checked before target inside a candle, one position at a time. Past results do not guarantee future results.")
    c = st.columns(5)
    days = c[0].slider("Days of history", 14, 180, 60)
    nsym = c[1].slider("Coins (top by volume)", 10, 60, 25)
    fee = c[2].number_input("Round-trip fee + slippage %", 0.05, 1.0, 0.20, 0.05)
    risk = c[3].number_input("Risk per trade ($)", 0.1, 100.0, float(max(0.1, risk_usd)), 0.1)
    days1 = c[4].slider("Days for 1-min strategies", 3, 14, 7)
    names = list(PRESETS) + ["F. Custom"]
    chosen = st.multiselect("Strategies to compare", names, default=list(PRESETS)[:3])
    params = {n: {**COMMON, **PRESETS[n]} for n in chosen if n in PRESETS}
    if "F. Custom" in chosen:
        with st.expander("F. Custom settings", expanded=True):
            p = {**COMMON, **PRESETS[st.selectbox("Start from", list(PRESETS), index=2)]}
            a, b, d, g = st.columns(4), st.columns(4), st.columns(4), st.columns(4)
            p["entry"] = a[0].selectbox("Entry", ["signal", "pullback"], index=["signal", "pullback"].index(p["entry"]))
            p["stop_atr"] = a[1].number_input("Stop (ATR)", 0.3, 4.0, float(p["stop_atr"]), 0.1)
            p["tp1_r"] = a[2].number_input("TP1 (R)", 0.3, 6.0, float(p["tp1_r"]), 0.1)
            p["tp1_frac"] = a[3].slider("Sell at TP1 (%)", 10, 100, int(p["tp1_frac"] * 100), 10) / 100
            p["tp2_r"] = b[0].number_input("TP2 (R)", 0.5, 10.0, float(p["tp2_r"]), 0.1)
            p["be_after_tp1"] = b[1].checkbox("Breakeven after TP1", p["be_after_tp1"])
            p["trail_bars"] = b[2].number_input("Trail bars (0=off)", 0, 16, int(p["trail_bars"]))
            p["time_bars"] = b[3].number_input("Time stop bars (0=off)", 0, 48, int(p["time_bars"]))
            p["vol_x"] = d[0].number_input("Volume x normal", 1.0, 10.0, float(p["vol_x"]), 0.5)
            p["buy_ratio"] = d[1].number_input("Buyer share", 0.45, 0.80, float(p["buy_ratio"]), 0.01)
            p["ret1h_hi"] = d[2].number_input("Max 1h gain %", 2.0, 20.0, float(p["ret1h_hi"]), 0.5)
            p["btc_filter"] = d[3].checkbox("Bitcoin filter", p["btc_filter"])
            p["tf"] = g[0].selectbox("Candle size", ["15m", "1m"], index=["15m", "1m"].index(p["tf"]))
            p["top_n"] = g[1].number_input("Only top-N risers (0=all)", 0, 30, int(p["top_n"]))
            p["fade_frac"] = g[2].number_input("Exit when volume fades below x of peak (0=off)", 0.0, 0.9, float(p["fade_frac"]), 0.05)
            p["min_stop_pct"] = g[3].number_input("Minimum stop %", 0.0, 3.0, float(p["min_stop_pct"]), 0.1)
            params["F. Custom"] = p
    if st.button("▶ Run backtest", type="primary", disabled=not params):
        bar = st.progress(0.0, "Starting…")
        try:
            res, info = run(params, days, nsym, fee, lambda x, m: bar.progress(min(x, 1.0), m), days1)
            st.session_state["bt"] = dict(res=res, info=info, risk=risk)
        except Exception as e:  # noqa: BLE001
            st.error(f"Backtest failed: {e}")
        bar.empty()
    bt = st.session_state.get("bt")
    if not bt:
        st.info("Pick strategies and press Run. First run downloads history (about 20-60 s); repeat runs are cached for an hour.")
        return
    res, risk = bt["res"], bt["risk"]
    rows, curves = [], {}
    for name, tr in res.items():
        inf = bt["info"][name]
        days, cut = inf["days"], inf["t0"] + 0.7 * (inf["t1"] - inf["t0"])
        m = metrics(tr, risk, days)
        mo, mn = metrics([t for t in tr if t["t_in"] <= cut], risk, days), metrics([t for t in tr if t["t_in"] > cut], risk, days)
        if m["trades"]:
            be = abs(m["avg_loss"]) / (m["avg_win"] + abs(m["avg_loss"])) * 100 if m["avg_win"] else 100.0
            rows.append({"Strategy": name, "Trades": m["trades"], "Per day": round(m["per_day"], 2), "Win %": round(m["win"]),
                         "Break-even win %": round(be), "Avg win R": round(m["avg_win"], 2), "Avg loss R": round(m["avg_loss"], 2),
                         "Expectancy R": round(m["exp"], 3), "Older 70% R": round(mo.get("exp", float("nan")), 3),
                         "Newer 30% R": round(mn.get("exp", float("nan")), 3), "Profit factor": round(m["pf"], 2),
                         "Max drawdown R": round(m["dd"], 1), f"P&L $ (risk ${risk:g})": round(m["pnl"], 2), "Verdict": verdict(m, mo, mn)})
            curves[name] = pd.Series(np.cumsum([t["R"] for t in tr]) * risk, index=pd.to_datetime([t["t_out"] for t in tr], unit="ms"))
        else:
            rows.append({"Strategy": name, "Trades": 0, "Verdict": "no trades"})
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    st.caption("Expectancy R = average result per trade in units of the amount risked. It must be above 0 in BOTH the older and newer data "
               "before you trust it. 'Win %' only matters compared with 'Break-even win %'.")
    if curves:
        st.line_chart(pd.concat(curves, axis=1).sort_index().ffill().fillna(0))
        pick = st.selectbox("Inspect trades", list(curves))
        tdf = pd.DataFrame(res[pick])
        tdf["time"] = pd.to_datetime(tdf.t_in, unit="ms")
        tdf = tdf[["time", "sym", "entry", "R", "ret_pct", "reason"]]
        st.write(tdf.reason.value_counts().to_frame("exits").T)
        st.dataframe(tdf, hide_index=True, width="stretch")
        st.download_button("⬇ Download trades CSV", tdf.to_csv(index=False), f"backtest_{pick[:1]}.csv")

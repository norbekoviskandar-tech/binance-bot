"""Data for Claude (dashboard tab). Downloads Binance futures candles (1h, 5m or 1m) for a set of coins and offers them as two CSV files:
Part 1 = the OLDER 65% of the time range (to analyse and build rules from), Part 2 = the NEWER 35% (a holdout, only used once at the end
to test rules that were written down before it was looked at). Columns: sym, t (ms, UTC), [time for 1h], o, h, l, c, qv (quote volume),
n (number of trades), tbq (taker-buy quote volume = the buyer share is tbq / qv)."""
import pandas as pd

import backtester as bt

BYTES_PER_ROW = {"1h": 95, "5m": 80, "1m": 80}
SIZES = {"1h": (180, 40), "5m": (30, 25), "1m": (14, 15)}  # default (days, coins) per candle size


def pick_coins(n, mode):
    """'volume' = the biggest coins; 'volatile' = the coins with the widest 24h range among the 80 biggest (character, not direction)."""
    if mode == "volume":
        return ["BTCUSDT"] + bt.universe(n)[:max(0, n - 1)]
    pool = set(bt.universe(80))
    rows = [x for x in bt.api("/fapi/v1/ticker/24hr") if x["symbol"] in pool and float(x["lowPrice"]) > 0]
    rows.sort(key=lambda x: -(float(x["highPrice"]) / float(x["lowPrice"]) - 1))
    return ["BTCUSDT"] + [x["symbol"] for x in rows if x["symbol"] != "BTCUSDT"][:max(0, n - 1)]  # BTC is always in as the market reference


def build(syms, days, interval="1h", progress=lambda x, m: None):
    parts = []
    for i, s in enumerate(syms):
        progress((i + 1) / len(syms), f"Downloading {interval} candles: {s} ({i + 1}/{len(syms)})")
        try:
            df = bt.history(s, days, interval)
        except Exception:
            continue
        if len(df) < 100:
            continue
        d = df[["t", "o", "h", "l", "c", "qv", "n", "tbq"]].copy()
        d.insert(0, "sym", s)
        if interval == "1h":
            d.insert(2, "time", pd.to_datetime(d.t, unit="ms").dt.strftime("%Y-%m-%d %H:%M"))
        parts.append(d)
    if not parts:
        raise RuntimeError("No candle data downloaded.")
    all_ = pd.concat(parts, ignore_index=True).sort_values(["t", "sym"]).reset_index(drop=True)
    lo, hi = int(all_.t.min()), int(all_.t.max())
    cut = int(lo + (hi - lo) * 0.65)
    csv = lambda d: d.to_csv(index=False, float_format="%.8g").encode()
    p1, p2 = all_[all_.t < cut], all_[all_.t >= cut]
    c1, c2 = csv(p1), csv(p2)
    ms = lambda t: pd.to_datetime(t, unit="ms").strftime("%Y-%m-%d %H:%M")
    return dict(p1=c1, p2=c2, n1=len(p1), n2=len(p2), coins=all_.sym.nunique(), lo=ms(lo), cut=ms(cut), hi=ms(hi), mb1=len(c1) / 1e6,
                mb2=len(c2) / 1e6, interval=interval, names=sorted(all_.sym.unique()))


def render():
    import streamlit as st
    st.subheader("📦 Data for Claude")
    st.caption("Downloads candles and splits them in two files. Upload **Part 1** to Claude so it can find rules. **Part 2** is a hidden test: "
               "do NOT upload it until Claude has written the rules down, otherwise the test means nothing.")
    c = st.columns(4)
    interval = c[0].selectbox("Candle size", ["1m", "5m", "1h"], key="de_int", help="1m or 5m for trades that last minutes; 1h for slower ideas.")
    d0, n0 = SIZES[interval]
    days = c[1].slider("Days of history", 3, 365, d0, key=f"de_days_{interval}")
    n = c[2].slider("Number of coins", 5, 60, n0, key=f"de_n_{interval}")
    mode = c[3].selectbox("Which coins", ["Most volatile (24h range)", "Biggest by volume"], key="de_mode",
                          help="Minute trades need coins that move more than the trading cost, so the volatile ones are the interesting ones. "
                               "Picked from today's snapshot (character, not direction).")
    mult = {"1m": 1440, "5m": 288, "1h": 24}[interval]
    est = n * days * mult * BYTES_PER_ROW[interval] / 1e6
    st.caption(f"About {n * days * mult:,} rows, roughly {est:.0f} MB in total (Part 1 about {est * 0.65:.0f} MB). "
               + ("⚠️ Large: keep Part 1 under about 25 MB so it can be uploaded." if est * 0.65 > 25 else ""))
    if st.button("▶ Prepare data", type="primary", key="de_run"):
        bar = st.progress(0.0, "Starting…")
        try:
            syms = pick_coins(n, "volume" if mode.startswith("Biggest") else "volatile")
            st.session_state["de_res"] = build(syms, days, interval, lambda x, m: bar.progress(min(x, 1.0), m))
            bar.empty()
        except Exception as ex:
            bar.empty()
            st.error(f"Could not finish: {ex}")
    r = st.session_state.get("de_res")
    if not r:
        st.info("Press Prepare data. The first run takes 1–3 minutes.")
        return
    st.success(f"{r['interval']} candles, {r['coins']} coins, {r['lo']} → {r['hi']} UTC. Part 1: {r['lo']} → {r['cut']} ({r['n1']:,} rows, {r['mb1']:.1f} MB). "
               f"Part 2: {r['cut']} → {r['hi']} ({r['n2']:,} rows, {r['mb2']:.1f} MB).")
    st.caption("Coins: " + ", ".join(r["names"]))
    d = st.columns(2)
    d[0].download_button("⬇️ Part 1 – older 65% (upload this to Claude)", r["p1"], f"claude_{r['interval']}_part1.csv", "text/csv", key="de_d1")
    d[1].download_button("⬇️ Part 2 – newer 35% HOLDOUT (wait until Claude asks)", r["p2"], f"claude_{r['interval']}_part2.csv", "text/csv", key="de_d2")

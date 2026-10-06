"""Data for Claude (dashboard tab). Downloads hourly Binance futures candles for the top coins and offers them as two CSV files:
Part 1 = the OLDER 65% of the time range (to analyse and build rules from), Part 2 = the NEWER 35% (a holdout, only used once at the end
to test rules that were written down before it was looked at). Columns: sym, t (ms), time (UTC), o, h, l, c, qv (quote volume),
n (number of trades), tbq (taker-buy quote volume = the buyer share is tbq / qv)."""
import pandas as pd

import backtester as bt

COLS = ["sym", "t", "time", "o", "h", "l", "c", "qv", "n", "tbq"]


def build(syms, days, progress=lambda x, m: None):
    parts = []
    for i, s in enumerate(syms):
        progress((i + 1) / len(syms), f"Downloading 1h candles: {s} ({i + 1}/{len(syms)})")
        try:
            df = bt.history(s, days, "1h")
        except Exception:
            continue
        if len(df) < 100:
            continue
        d = df[["t", "o", "h", "l", "c", "qv", "n", "tbq"]].copy()
        d.insert(0, "sym", s)
        d.insert(2, "time", pd.to_datetime(d.t, unit="ms").dt.strftime("%Y-%m-%d %H:%M"))
        parts.append(d)
    if not parts:
        raise RuntimeError("No candle data downloaded.")
    all_ = pd.concat(parts, ignore_index=True)[COLS].sort_values(["t", "sym"]).reset_index(drop=True)
    lo, hi = int(all_.t.min()), int(all_.t.max())
    cut = int(lo + (hi - lo) * 0.65)
    csv = lambda d: d.to_csv(index=False, float_format="%.8g").encode()
    p1, p2 = all_[all_.t < cut], all_[all_.t >= cut]
    ms = lambda t: pd.to_datetime(t, unit="ms").strftime("%Y-%m-%d")
    return dict(p1=csv(p1), p2=csv(p2), n1=len(p1), n2=len(p2), coins=all_.sym.nunique(), lo=ms(lo), cut=ms(cut), hi=ms(hi),
                mb1=len(csv(p1)) / 1e6, mb2=len(csv(p2)) / 1e6)


def render():
    import streamlit as st
    st.subheader("📦 Data for Claude")
    st.caption("Downloads hourly candles for the top Binance futures coins and splits them in two files. Upload **Part 1** to Claude so it can "
               "find rules. **Part 2** is a hidden test: do NOT upload it until Claude has written the rules down, otherwise the test means nothing.")
    c = st.columns(2)
    days = c[0].slider("Days of history", 90, 365, 180, key="de_days")
    n = c[1].slider("Number of coins (top by volume)", 10, 60, 40, key="de_n")
    if st.button("▶ Prepare data", type="primary", key="de_run"):
        bar = st.progress(0.0, "Starting…")
        try:
            syms = ["BTCUSDT"] + bt.universe(n)[:max(0, n - 1)]
            st.session_state["de_res"] = build(syms, days, lambda x, m: bar.progress(min(x, 1.0), m))
            bar.empty()
        except Exception as ex:
            bar.empty()
            st.error(f"Could not finish: {ex}")
    r = st.session_state.get("de_res")
    if not r:
        st.info("Press Prepare data. It takes 1–3 minutes the first time.")
        return
    st.success(f"{r['coins']} coins, {r['lo']} → {r['hi']}. Part 1: {r['lo']} → {r['cut']} ({r['n1']:,} rows, {r['mb1']:.1f} MB). "
               f"Part 2: {r['cut']} → {r['hi']} ({r['n2']:,} rows, {r['mb2']:.1f} MB).")
    d = st.columns(2)
    d[0].download_button("⬇️ Part 1 – older 65% (upload this to Claude)", r["p1"], "claude_data_part1.csv", "text/csv", key="de_d1")
    d[1].download_button("⬇️ Part 2 – newer 35% HOLDOUT (wait until Claude asks)", r["p2"], "claude_data_part2.csv", "text/csv", key="de_d2")

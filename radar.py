"""Live Top-10 radar (read-only, public data): today's top rising coins and how fast their volume is moving RIGHT NOW."""
import time

import numpy as np
import pandas as pd

import backtester as bt
import velocity as vel

P = {**bt.COMMON, **bt.PRESETS[bt.FAST]}


def scan(top_n=10):
    tick = bt.api("/fapi/v1/ticker/24hr")
    cand = sorted((x for x in tick if x["symbol"].endswith("USDT") and "_" not in x["symbol"] and float(x["quoteVolume"]) >= P["min_vol24"]),
                  key=lambda x: -float(x["priceChangePercent"]))[:top_n]
    fund = {x["symbol"]: float(x["lastFundingRate"]) for x in bt.api("/fapi/v1/premiumIndex")}
    btc = bt.frame(bt.api("/fapi/v1/klines", {"symbol": "BTCUSDT", "interval": "1m", "limit": 1500}))
    btc_ok = bool(((btc.c > btc.c.ewm(span=1200, adjust=False).mean()) & (btc.c / btc.c.shift(60) > 1)).iloc[-1])
    rows = []
    for rank, x in enumerate(cand, 1):
        s = x["symbol"]
        df = bt.frame(bt.api("/fapi/v1/klines", {"symbol": s, "interval": "1m", "limit": 120}))
        f = vel.features_1m(df).iloc[[-1]].copy()
        r = f.iloc[0]
        vol24 = float(x["quoteVolume"])
        q15 = float(df.qv.iloc[-15:].sum())
        f["rank24"], f["vol24"], f["btc_ok"] = rank, vol24, btc_ok
        sig = bool(bt.setup_mask(f, P).iloc[0])
        try:  # open interest = how big the futures market in this coin is; rising OI + rising price = new money coming in
            h = bt.api("/futures/data/openInterestHist", {"symbol": s, "period": "5m", "limit": 7})
            oi = float(h[-1]["sumOpenInterestValue"])
            oichg = (oi / float(h[-4]["sumOpenInterestValue"]) - 1) * 100
        except Exception:  # noqa: BLE001
            oi, oichg = np.nan, np.nan
        met = int(sum([r.vol_x >= P["vol_x"], r.vol_accel >= P["accel_min"], r.buy_ratio >= P["buy_ratio"],
                       r.trades_x >= P["trades_x"], bool(r.breakout), btc_ok]))
        if sig and not oichg < 0:
            label = "🟢 ENTRY"
        elif sig:
            label = "🟡 signal, but open interest falling"
        else:
            label = f"👀 {met}/6" if met >= 4 else f"{met}/6"
        rows.append({"#": rank, "Coin": s.replace("USDT", ""), "24h %": float(x["priceChangePercent"]), "15m %": r.ret1h,
                     "Volume pace x": q15 / (vol24 / 96), "Surge x (3m)": r.vol_x, "Accel x": r.vol_accel, "Buyers %": r.buy_ratio * 100,
                     "Trades x": r.trades_x, "OI chg 15m %": oichg, "Vol/OI 15m %": q15 / oi * 100 if oi == oi else np.nan,
                     "Funding %": fund.get(s, np.nan) * 100, "Breakout": bool(r.breakout), "Signal": label})
        time.sleep(0.1)
    return pd.DataFrame(rows), btc_ok


def render():
    import streamlit as st
    st.subheader("⚡ Top-10 Radar")
    st.caption("Read-only. Ranks today's top rising coins and shows how fast their volume is moving now. "
               "Volume pace = last 15 min traded vs an average 15-min slice of the day. Surge = last 3 min vs its own normal. "
               "Accel = last 3 min vs the 3 min before. Vol/OI = share of the whole futures market traded in 15 min.")
    c = st.columns(3)
    n = c[0].slider("Coins to watch", 5, 20, 10)
    auto = c[1].checkbox("Auto-refresh every 30 s", value=False)
    if c[2].button("🔄 Refresh now") or "radar" not in st.session_state:
        try:
            with st.spinner("Reading Binance…"):
                st.session_state["radar"] = (scan(n), time.strftime("%H:%M:%S UTC", time.gmtime()))
        except Exception as e:  # noqa: BLE001
            st.error(f"Radar failed: {e}")
    if "radar" in st.session_state:
        (df, btc_ok), ts = st.session_state["radar"]
        st.markdown(f"**Updated {ts}.** Bitcoin filter: {'✅ up' if btc_ok else '⛔ not up (entries blocked)'}")
        st.dataframe(df.style.format({"24h %": "{:+.1f}", "15m %": "{:+.2f}", "Volume pace x": "{:.1f}", "Surge x (3m)": "{:.1f}", "Accel x": "{:.1f}",
                                      "Buyers %": "{:.0f}", "Trades x": "{:.1f}", "OI chg 15m %": "{:+.2f}", "Vol/OI 15m %": "{:.1f}",
                                      "Funding %": "{:+.3f}"}, na_rep="n/a"), hide_index=True, width="stretch")
        st.caption("🟢 ENTRY = all rules of strategy G pass now (top-10 riser, volume surge and accelerating, buyers dominate, breakout, Bitcoin up) "
                   "and open interest is not falling. Check strategy G in the Backtest tab before trusting it.")
    if auto:
        time.sleep(30)
        st.rerun()

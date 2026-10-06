"""Live Top-10 radar (read-only, public data): top rising coins, how fast volume moves, and whether buying is cooling off."""
import time

import numpy as np
import pandas as pd

import backtester as bt
import velocity as vel

P = {**bt.COMMON, **bt.PRESETS[bt.FAST]}
WHY = {"sell": "sellers > buyers", "flip": "net buying flipped negative", "div": "price high but weak buying",
       "fade": "volume fading", "rej": "rejection candle"}


def _row(s, x, rank, fund, btc_ok, held):
    df = bt.frame(bt.api("/fapi/v1/klines", {"symbol": s, "interval": "1m", "limit": 120}))
    f = vel.features_1m(df).iloc[[-1]].copy()
    r = f.iloc[0]
    cl = vel.cooling(df).iloc[-1]
    cool = int(cl["cool"])
    why = ", ".join(v for k, v in WHY.items() if bool(cl[k])) or "-"
    vol24 = float(x["quoteVolume"])
    q15 = float(df.qv.iloc[-15:].sum())
    f["rank24"], f["vol24"], f["btc_ok"] = rank or 99, vol24, btc_ok
    sig = bool(bt.setup_mask(f, P).iloc[0])
    try:  # open interest = size of the futures market in this coin; rising OI + rising price = new money entering
        h = bt.api("/futures/data/openInterestHist", {"symbol": s, "period": "5m", "limit": 7})
        oi = float(h[-1]["sumOpenInterestValue"])
        oichg = (oi / float(h[-4]["sumOpenInterestValue"]) - 1) * 100
    except Exception:  # noqa: BLE001
        oi, oichg = np.nan, np.nan
    met = int(sum([r.vol_x >= P["vol_x"], r.vol_accel >= P["accel_min"], r.buy_ratio >= P["buy_ratio"],
                   r.trades_x >= P["trades_x"], bool(r.breakout), btc_ok]))
    if held:
        label = "🧊 COOLING: consider exit" if cool >= 2 else ("⚠️ weakening" if cool == 1 else "💪 buyers still in control")
    elif sig and cool >= 2:
        label = "🧊 cooling: skip"
    elif sig and not oichg < 0:
        label = "🟢 ENTRY"
    elif sig:
        label = "🟡 signal, open interest falling"
    else:
        label = f"👀 {met}/6" if met >= 4 else f"{met}/6"
    return {"#": rank or "held", "Coin": s.replace("USDT", ""), "24h %": float(x["priceChangePercent"]), "15m %": r.ret1h,
            "Volume pace x": q15 / (vol24 / 96), "Surge x (3m)": r.vol_x, "Accel x": r.vol_accel, "Buyers %": r.buy_ratio * 100,
            "Cooling 0-5": cool, "Cooling signs": why, "OI chg 15m %": oichg, "Vol/OI 15m %": q15 / oi * 100 if oi == oi else np.nan,
            "Funding %": fund.get(s, np.nan) * 100, "Signal": label}


def scan(top_n=10, held=()):
    tick = bt.api("/fapi/v1/ticker/24hr")
    by = {x["symbol"]: x for x in tick}
    cand = sorted((x for x in tick if x["symbol"].endswith("USDT") and "_" not in x["symbol"] and float(x["quoteVolume"]) >= P["min_vol24"]),
                  key=lambda x: -float(x["priceChangePercent"]))[:top_n]
    fund = {x["symbol"]: float(x["lastFundingRate"]) for x in bt.api("/fapi/v1/premiumIndex")}
    btc = bt.frame(bt.api("/fapi/v1/klines", {"symbol": "BTCUSDT", "interval": "1m", "limit": 1500}))
    btc_ok = bool(((btc.c > btc.c.ewm(span=1200, adjust=False).mean()) & (btc.c / btc.c.shift(60) > 1)).iloc[-1])
    rows = []
    for sym in dict.fromkeys(f"{h.strip().upper().replace('USDT', '')}USDT" for h in held if h.strip()):  # coins you hold come first
        if sym in by:
            rows.append(_row(sym, by[sym], None, fund, btc_ok, True))
            time.sleep(0.1)
    for rank, x in enumerate(cand, 1):
        rows.append(_row(x["symbol"], x, rank, fund, btc_ok, False))
        time.sleep(0.1)
    return pd.DataFrame(rows), btc_ok


def render():
    import streamlit as st
    st.subheader("⚡ Top-10 Radar")
    st.caption("Read-only. Volume pace = last 15 min traded vs an average 15-min slice of the day. Surge = last 3 min vs its own normal. "
               "Accel = last 3 min vs the 3 min before. Vol/OI = share of the whole futures market traded in 15 min. "
               "Cooling 0-5 compares aggressive buying with aggressive selling: 2 or more means the move is losing steam.")
    c = st.columns(4)
    n = c[0].slider("Coins to watch", 5, 20, 10)
    held = c[1].text_input("Coins you hold (e.g. SOL, DOGE)", "")
    auto = c[2].checkbox("Auto-refresh every 30 s", value=False)
    if c[3].button("🔄 Refresh now") or "radar" not in st.session_state:
        try:
            with st.spinner("Reading Binance…"):
                st.session_state["radar"] = (scan(n, held.split(",")), time.strftime("%H:%M:%S UTC", time.gmtime()))
        except Exception as e:  # noqa: BLE001
            st.error(f"Radar failed: {e}")
    if "radar" in st.session_state:
        (df, btc_ok), ts = st.session_state["radar"]
        st.markdown(f"**Updated {ts}.** Bitcoin filter: {'✅ up' if btc_ok else '⛔ not up (entries blocked)'}")
        st.dataframe(df.style.format({"24h %": "{:+.1f}", "15m %": "{:+.2f}", "Volume pace x": "{:.1f}", "Surge x (3m)": "{:.1f}", "Accel x": "{:.1f}",
                                      "Buyers %": "{:.0f}", "OI chg 15m %": "{:+.2f}", "Vol/OI 15m %": "{:.1f}", "Funding %": "{:+.3f}"},
                                     na_rep="n/a"), hide_index=True, width="stretch")
        st.caption("🟢 ENTRY = every rule of strategy G passes, open interest is not falling and the cooling score is below 2. "
                   "Rows tagged 'held' are the coins you typed in: they show whether buyers are still in control. Test strategy I in the Backtest tab "
                   "to see if exiting on the cooling score actually improves results.")
    if auto:
        time.sleep(30)
        st.rerun()

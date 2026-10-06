"""Volume-velocity features on 1-minute candles (public data). Column names match the 15m features so the
same rule/backtest code works on both. NOTE: 'ret1h' here means the 15-minute return."""
import numpy as np
import pandas as pd


def features_1m(df):
    c, h, l, qv = df.c, df.h, df.l, df.qv
    f = pd.DataFrame(index=df.index)
    f["ret1h"] = (c / c.shift(15) - 1) * 100  # 15-minute return
    q3 = qv.rolling(3).sum()
    f["vol_x"] = q3 / (qv.rolling(60).median().shift(3) * 3).replace(0, np.nan)  # last 3 min volume vs its normal 3 min
    f["vol_accel"] = q3 / q3.shift(3).replace(0, np.nan)  # velocity: is volume speeding up vs the 3 min before
    f["buy_ratio"] = df.tbq.rolling(3).sum() / q3.replace(0, np.nan)  # aggressive buyers' share, last 3 min
    f["trades_x"] = df.n.rolling(3).sum() / (df.n.rolling(60).median().shift(3) * 3).replace(0, np.nan)
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    f["atr"] = tr.rolling(14).mean()
    f["ext_atr"] = (c - c.ewm(span=20, adjust=False).mean()) / f.atr
    f["level"] = h.rolling(30).max().shift(1)
    f["breakout"] = c > f.level
    f["bar_buy"] = df.tbq / qv.replace(0, np.nan)
    f["vol24"] = qv.rolling(1440).sum()  # traded value over the last 24h
    f["pace"] = qv.rolling(15).sum() / (f.vol24 / 96)  # last 15 min volume as a multiple of an average 15 min slice of the day
    f["ret24"] = (c / c.shift(1440) - 1) * 100
    return f


def cooling(df):
    """Is the move running out of steam? Compares aggressive BUY volume against aggressive SELL volume (taker data).
    Five warning signs, score 0-5 (each True = 1):
      sell   sellers outweigh buyers over the last 3 candles (buyer share < 48%)
      flip   net buying (buys minus sells) was positive and just turned negative
      div    price is at its recent high but net buying is less than half of its recent peak (weak push = divergence)
      fade   total volume fell below half of its recent peak
      rej    a rejection candle: long upper wick, closed red, on above-normal volume"""
    qv, buy = df.qv, df.tbq
    d = buy - (qv - buy)  # net aggressive flow per candle (USD)
    q3, b3, d3 = qv.rolling(3).sum(), buy.rolling(3).sum(), d.rolling(3).sum()
    out = pd.DataFrame(index=df.index)
    out["sell"] = (b3 / q3.replace(0, np.nan)) < 0.48
    out["flip"] = (d3 < 0) & (d3.shift(3) > 0)
    peak = d3.rolling(15).max()
    out["div"] = (df.c >= df.h.rolling(15).max() * 0.998) & (peak > 0) & (d3 < 0.5 * peak)
    out["fade"] = q3 < 0.5 * q3.rolling(15).max()
    upper = df.h - np.maximum(df.o, df.c)
    out["rej"] = (upper / (df.h - df.l).replace(0, np.nan) >= 0.6) & (df.c < df.o) & (qv > qv.rolling(30).median())
    out = out.fillna(False).astype(bool)
    out["cool"] = out[["sell", "flip", "div", "fade", "rej"]].sum(axis=1)
    return out

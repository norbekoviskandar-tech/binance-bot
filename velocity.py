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

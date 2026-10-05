#!/usr/bin/env python3
"""Gainers-continuation study + live filter. Uses PUBLIC Binance data only (no API key).

  python gainers_study.py study --symbols 120      # does the rule beat plain "buy gainers"? (history)
  python gainers_study.py live  --top 40           # which of today's top gainers pass the rule right now

Idea: a coin that is already up 1.5-8% in the last hour keeps going only when the move is
backed by (1) volume well above its own normal, still accelerating, (2) aggressive BUYERS
(taker-buy share of volume), (3) many more trades (many different participants, not one whale),
(4) a breakout of the recent range, (5) calm before the move (squeeze), and (6) not already
stretched far above its EMA. The study measures which of these actually add edge.
Entries are simulated at the NEXT candle open and fees+slippage are charged (FEE).
"""
import argparse
import time

import pandas as pd
import requests

BASE = "https://fapi.binance.com"
FEE = 0.0020  # round-trip fees + slippage assumption (0.20%)
HORIZONS = (4, 8, 16)  # 15m bars -> 1h, 2h, 4h
COOLDOWN = 16  # bars between signals on the same symbol

RULE = dict(ret1h_min=1.5, ret1h_max=8.0, vol_x_min=3.0, vol_accel_min=1.0,
            buy_ratio_min=0.57, trades_x_min=2.5, ext_atr_max=3.0, need_breakout=True)


def api(path, params=None):
    r = requests.get(BASE + path, params=params, timeout=15)
    r.raise_for_status()
    return r.json()


def klines(symbol, limit=1500, interval="15m"):
    raw = api("/fapi/v1/klines", {"symbol": symbol, "interval": interval, "limit": limit})
    df = pd.DataFrame(raw, columns=["t", "o", "h", "l", "c", "v", "ct", "qv", "n", "tbb", "tbq", "_"])
    for k in ["o", "h", "l", "c", "v", "qv", "n", "tbb", "tbq"]:
        df[k] = df[k].astype(float)
    return df.iloc[:-1].reset_index(drop=True)  # drop the still-open candle


def features(df):
    c, h, l = df.c, df.h, df.l
    f = pd.DataFrame(index=df.index)
    f["ret1h"] = (c / c.shift(4) - 1) * 100
    qv4 = df.qv.rolling(4).sum()
    f["vol_x"] = qv4 / (df.qv.rolling(96).median().shift(4) * 4)  # volume vs normal, baseline taken BEFORE the move
    f["vol_accel"] = f.vol_x / f.vol_x.shift(4)  # >1 = volume still rising
    f["buy_ratio"] = df.tbq.rolling(4).sum() / qv4  # share of volume from aggressive buyers
    f["trades_x"] = df.n.rolling(4).sum() / (df.n.rolling(96).median().shift(4) * 4)  # participation
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr = tr.rolling(14).mean()
    f["squeeze"] = tr.rolling(24).mean().shift(4) / tr.rolling(96).mean().shift(4)  # <1 = calm before the move
    f["ext_atr"] = (c - c.ewm(span=20, adjust=False).mean()) / atr  # how stretched above EMA20
    f["breakout"] = c > h.rolling(32).max().shift(1)
    return f


def signal(f, r=RULE):
    s = (f.ret1h.between(r["ret1h_min"], r["ret1h_max"]) & (f.vol_x >= r["vol_x_min"])
         & (f.vol_accel >= r["vol_accel_min"]) & (f.buy_ratio >= r["buy_ratio_min"])
         & (f.trades_x >= r["trades_x_min"]) & (f.ext_atr <= r["ext_atr_max"]))
    return s & f.breakout if r["need_breakout"] else s


def pick(mask):
    out, last = [], -10**9
    for i in mask[mask].index:
        if i - last >= COOLDOWN:
            out.append(i)
            last = i
    return out


def outcomes(df, f, idx, symbol):
    rows = []
    for i in idx:
        if i + max(HORIZONS) + 1 >= len(df):
            continue
        e = df.o[i + 1]  # enter next candle open
        row = {"sym": symbol, "t": df.t[i], **f.loc[i, ["vol_x", "buy_ratio", "trades_x", "squeeze", "ext_atr", "breakout", "vol_accel"]].to_dict()}
        for hz in HORIZONS:
            row[f"r{hz}"] = (df.c[i + hz] / e - 1) * 100 - FEE * 100
        row["mae16"] = (df.l[i + 1:i + 17].min() / e - 1) * 100
        rows.append(row)
    return rows


def summ(name, d):
    if len(d) == 0:
        print(f"{name:36s} n=0")
        return
    print(f"{name:36s} n={len(d):4d}  1h={d.r4.mean():+.2f}%  2h={d.r8.mean():+.2f}% (med {d.r8.median():+.2f})  "
          f"4h={d.r16.mean():+.2f}%  win2h={(d.r8 > 0).mean() * 100:.0f}%  worst-dip4h={d.mae16.mean():.2f}%")


def study(nsym, bars):
    tick = api("/fapi/v1/ticker/24hr")
    syms = [x["symbol"] for x in sorted((x for x in tick if x["symbol"].endswith("USDT")),
                                        key=lambda x: -float(x["quoteVolume"]))][:nsym]
    base_rows, sig_rows = [], []
    for k, s in enumerate(syms, 1):
        try:
            df = klines(s, bars)
        except Exception as e:  # noqa: BLE001
            print("skip", s, e)
            continue
        if len(df) < 300:
            continue
        f = features(df)
        gain = f.ret1h.between(RULE["ret1h_min"], RULE["ret1h_max"])  # baseline = every plain "gainer" moment
        base_rows += outcomes(df, f, pick(gain), s)
        sig_rows += outcomes(df, f, pick(signal(f)), s)
        time.sleep(0.15)
        print(f"\r{k}/{len(syms)} symbols", end="", flush=True)
    print()
    base, sig = pd.DataFrame(base_rows), pd.DataFrame(sig_rows)
    print("\n=== Does the rule beat plain 'buy a gainer'? (fees included) ===")
    summ("ALL gainers (baseline)", base)
    summ("RULE signals", sig)
    if len(sig) >= 10:
        cut = sig.t.quantile(0.7)
        print("\n=== Out-of-sample check (rule picked on all data, so judge TEST skeptically) ===")
        summ("first 70% of time", sig[sig.t <= cut])
        summ("last 30% of time", sig[sig.t > cut])
    print("\n=== Which single condition adds edge? (within plain gainers) ===")
    conds = {"buy_ratio >= 0.57": base.buy_ratio >= 0.57, "vol_x >= 3": base.vol_x >= 3,
             "trades_x >= 2.5": base.trades_x >= 2.5, "vol_accel >= 1": base.vol_accel >= 1,
             "squeeze < 1 (calm before)": base.squeeze < 1, "ext_atr <= 3 (not stretched)": base.ext_atr <= 3,
             "breakout": base.breakout.astype(bool)} if len(base) else {}
    for name, m in conds.items():
        summ(f"{name}  [YES]", base[m])
        summ(f"{name}  [NO]", base[~m])
    print("\nRead it like this: a condition only earns a place in RULE if [YES] beats [NO] by more than the noise\n"
          "(small n = noise) and the last-30% block agrees with the first-70%. Edit RULE at the top and re-run.")


def live(top):
    tick = api("/fapi/v1/ticker/24hr")
    cand = sorted((x for x in tick if x["symbol"].endswith("USDT") and float(x["quoteVolume"]) > 2e7),
                  key=lambda x: -float(x["priceChangePercent"]))[:top]
    print(f"{'symbol':14s}{'24h%':>7s}{'1h%':>6s}{'volx':>6s}{'buy%':>6s}{'trdx':>6s}{'ext':>6s}  pass")
    for x in cand:
        f = features(klines(x["symbol"], 200)).iloc[[-1]]
        r = f.iloc[0]
        ok = bool(signal(f).iloc[0])
        print(f"{x['symbol']:14s}{float(x['priceChangePercent']):7.1f}{r.ret1h:6.1f}{r.vol_x:6.1f}"
              f"{r.buy_ratio * 100:6.0f}{r.trades_x:6.1f}{r.ext_atr:6.1f}  {'YES' if ok else '-'}")
        time.sleep(0.1)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("study")
    a.add_argument("--symbols", type=int, default=120)
    a.add_argument("--bars", type=int, default=1500)
    b = sub.add_parser("live")
    b.add_argument("--top", type=int, default=40)
    args = ap.parse_args()
    study(args.symbols, args.bars) if args.cmd == "study" else live(args.top)

#!/usr/bin/env python3
"""
READ-ONLY Binance momentum scanner.

* Uses ONLY Binance's public market-data addresses. No API key, no login, no orders.
  (There is no code in this file that can trade, because trading needs a key.)
* Every 5 minutes it checks all liquid USDT spot coins and flags the ones that pass ALL rules.
* Every flagged coin is saved in signals.csv, and the script later fills in the price
  15, 30 and 60 minutes after the signal, so you can measure the real win rate.

How to run:
    python scanner.py            keep scanning every 5 minutes (stop with Ctrl+C)
    python scanner.py --once     scan one time and exit (good for a first test)
    python scanner.py --report   show win-rate statistics from signals.csv
"""
import argparse
import csv
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

# =====================================================================================
# SETTINGS  (the only part you might want to change)
# =====================================================================================
MIN_VOLUME_USD = 5_000_000      # skip coins with less than $5M traded in 24h
CANDLE = "15m"                  # candle size used for the signal
LOOP_SECONDS = 300              # scan every 5 minutes
VOLUME_RATIO_MIN = 1.5          # latest candle volume must be >= 1.5x the average of the 20 before it
RSI_MIN, RSI_MAX = 45, 70       # RSI must be inside this range
CANDLES_IN_4H = 16              # 16 candles x 15 minutes = 4 hours
MIN_4H_CHANGE_PCT = 1.0         # minimum 4h change % to be considered a gainer (was 0.42%, now 1.0%+)
BTC_DROP_PAUSE_PCT = -2.0       # pause all signals if Bitcoin fell more than 2% in the last hour
FOLLOW_UP_MINUTES = (15, 30, 60)    # when to record the price after a signal
ROUND_TRIP_FEE_PCT = 0.2        # Binance spot fee is about 0.1% per side; used only in the report
CSV_FILE = Path("signals.csv")

# Binance addresses for public data. Using FUTURES API to match bot.py
HOSTS = ["https://fapi.binance.com"]
STABLECOINS = {"USDC", "FDUSD", "TUSD", "BUSD", "USDP", "DAI", "USDE", "EUR", "AEUR", "PYUSD", "USD1"}

CSV_FIELDS = [
    "detected_at_utc", "candle_close_utc", "symbol", "price_at_signal", "volume_ratio", "rsi",
    "change_4h_pct", "btc_change_4h_pct",
    "price_15m", "pct_15m", "price_30m", "pct_30m", "price_60m", "pct_60m",
]

SESSION = requests.Session()


# =====================================================================================
# TALKING TO BINANCE (public data only)
# =====================================================================================
def get(path, params=None):
    """Download one public page of data from Binance. Retries and tries other addresses if it fails."""
    error = "unknown error"
    for attempt in range(3):
        for host in HOSTS:
            try:
                r = SESSION.get(host + path, params=params, timeout=15)
            except requests.RequestException as e:
                error = str(e)
                continue
            if r.status_code == 200:
                return r.json()
            if r.status_code in (418, 429):          # Binance says "too many requests"
                wait = int(r.headers.get("Retry-After", 30))
                print(f"Binance asked us to slow down. Waiting {wait} seconds...")
                time.sleep(wait)
                error = "rate limited"
                break
            error = f"HTTP {r.status_code} from {host}"
            if r.status_code == 451:
                error += " (your network or country is blocked by Binance)"
        else:                                       # every address failed this round: wait, then retry
            time.sleep(2 ** attempt)
    raise RuntimeError(f"Could not get {path}: {error}")


def get_candles(symbol, interval, limit=100, only_closed=True):
    """Download candles into a table. By default the unfinished (still forming) candle is thrown away."""
    raw = get("/fapi/v1/klines", {"symbol": symbol, "interval": interval, "limit": limit})
    df = pd.DataFrame(raw).iloc[:, :7]
    df.columns = ["open_time", "open", "high", "low", "close", "volume", "close_time"]
    for col in ("open", "high", "low", "close", "volume"):
        df[col] = df[col].astype(float)
    df["close_time"] = df["close_time"].astype("int64")
    if only_closed:
        df = df[df["close_time"] < int(time.time() * 1000)]     # keep only candles that already ended
    return df.reset_index(drop=True)


def liquid_pairs():
    """All tradable USDT perpetual futures pairs with at least MIN_VOLUME_USD traded in the last 24h, biggest first."""
    info = get("/fapi/v1/exchangeInfo")
    ok = {s["symbol"] for s in info["symbols"]
          if s["status"] == "TRADING" and s.get("contractType") == "PERPETUAL"
          and s["quoteAsset"] == "USDT" and s["baseAsset"] not in STABLECOINS}
    tickers = get("/fapi/v1/ticker/24hr")
    rows = [t for t in tickers if t["symbol"] in ok and float(t["quoteVolume"]) >= MIN_VOLUME_USD]
    rows.sort(key=lambda t: -float(t["quoteVolume"]))
    return [t["symbol"] for t in rows]


# =====================================================================================
# THE INDICATORS
# =====================================================================================
def calc_rsi(close, period=14):
    """RSI: 0-100 strength meter. Above 70 = overheated, below 30 = beaten down."""
    change = close.diff()
    gains = change.clip(lower=0)
    losses = -change.clip(upper=0)
    avg_gain = gains.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = losses.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, float("nan"))
    return 100 - 100 / (1 + rs)


def calc_macd(close):
    """MACD (12, 26, 9): returns the MACD line and the signal line."""
    macd_line = close.ewm(span=12, adjust=False).mean() - close.ewm(span=26, adjust=False).mean()
    signal_line = macd_line.ewm(span=9, adjust=False).mean()
    return macd_line, signal_line


def change_4h_pct(df):
    """Percent price change over the last 4 hours (16 candles of 15 minutes), using closed candles."""
    return (df["close"].iloc[-1] / df["close"].iloc[-1 - CANDLES_IN_4H] - 1) * 100


def atr(df, period=14):
    """Average True Range - volatility indicator."""
    pc = df["close"].shift()
    tr = pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def evaluate(df):
    """Measure everything we need on the LAST CLOSED candle. Returns None if there is too little data."""
    if len(df) < 40:
        return None
    avg_prev_volume = df["volume"].iloc[-21:-1].mean()                  # average of the 20 candles before the latest
    volume_ratio = df["volume"].iloc[-1] / avg_prev_volume if avg_prev_volume > 0 else 0.0
    macd_line, signal_line = calc_macd(df["close"])
    crossed_up = (macd_line.iloc[-2] <= signal_line.iloc[-2]) and (macd_line.iloc[-1] > signal_line.iloc[-1])
    return {
        "price": df["close"].iloc[-1],
        "volume_ratio": float(volume_ratio),
        "rsi": float(calc_rsi(df["close"]).iloc[-1]),
        "macd_cross_up": bool(crossed_up),
        "change_4h": float(change_4h_pct(df)),
        "candle_close_ms": int(df["close_time"].iloc[-1]),
    }


def is_signal(m, btc_change_4h):
    """A coin is flagged ONLY if every one of these is true."""
    return (
        m["volume_ratio"] >= VOLUME_RATIO_MIN               # 1. volume surge
        and RSI_MIN <= m["rsi"] <= RSI_MAX                  # 2. RSI in the healthy zone
        and m["macd_cross_up"]                              # 3. MACD just crossed up on the latest closed candle
        and m["change_4h"] > btc_change_4h                  # 4. beating Bitcoin over 4 hours
        and m["change_4h"] >= MIN_4H_CHANGE_PCT             # 5. minimum 4h gain (at least 1%)
    )


def btc_change_last_hour():
    """Bitcoin's percent change over about the last 60 minutes (uses 1-minute candles, includes the live one)."""
    df = get_candles("BTCUSDT", "1m", limit=61, only_closed=False)
    return (df["close"].iloc[-1] / df["open"].iloc[0] - 1) * 100


# =====================================================================================
# THE CSV LOG
# =====================================================================================
def read_rows():
    if not CSV_FILE.exists():
        return []
    with CSV_FILE.open(newline="") as f:
        return list(csv.DictReader(f))


def write_rows(rows):
    with CSV_FILE.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in CSV_FIELDS})


def utc_text(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def utc_epoch(text):
    return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()


def update_followups():
    """For old signals, fill in the price 15/30/60 minutes later (read from 1-minute candles, so it is exact)."""
    rows = read_rows()
    changed = False
    now = time.time()
    for r in rows:
        t0 = utc_epoch(r["detected_at_utc"])
        entry = float(r["price_at_signal"])
        for m in FOLLOW_UP_MINUTES:
            if r.get(f"price_{m}m"):
                continue
            if now < t0 + m * 60 + 70:                    # that moment has not fully passed yet
                continue
            target_ms = int((t0 + m * 60) // 60 * 60 * 1000)
            try:
                k = get("/fapi/v1/klines", {"symbol": r["symbol"], "interval": "1m", "startTime": target_ms, "limit": 1})
            except RuntimeError:
                continue
            if k:
                price = float(k[0][1])                    # price at the start of that minute
                r[f"price_{m}m"] = price
                r[f"pct_{m}m"] = round((price / entry - 1) * 100, 3)
                changed = True
    if changed:
        write_rows(rows)


# =====================================================================================
# ONE FULL SCAN
# =====================================================================================
def run_scan(quiet=False):
    """Run a full scan. If quiet=True, returns results dict without printing or writing files."""
    now_text = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    if not quiet:
        update_followups()                                    # first, finish measuring older signals

    btc_1h = btc_change_last_hour()
    if btc_1h < BTC_DROP_PAUSE_PCT:                       # safety rule: Bitcoin is dropping fast
        if not quiet:
            print(f"[{now_text}] PAUSED: Bitcoin is {btc_1h:+.2f}% in the last hour (limit {BTC_DROP_PAUSE_PCT}%). No signals.")
        return {"paused": True, "btc_1h": btc_1h, "reason": "btc_drop"}

    btc_4h = change_4h_pct(get_candles("BTCUSDT", CANDLE, limit=100))
    pairs = [p for p in liquid_pairs() if p != "BTCUSDT"]
    if not quiet:
        print(f"[{now_text}] Checking {len(pairs)} coins. Bitcoin: {btc_1h:+.2f}% 1h, {btc_4h:+.2f}% 4h")

    already_logged = {(r["symbol"], r["candle_close_utc"]) for r in read_rows()} if not quiet else set()
    hits = []
    for symbol in pairs:
        try:
            m = evaluate(get_candles(symbol, CANDLE, limit=100))
        except Exception as e:                             # one bad coin must not stop the scan
            if not quiet:
                print(f"  skipped {symbol}: {e}")
            continue
        if m and is_signal(m, btc_4h):
            candle_close = utc_text(m["candle_close_ms"] / 1000)
            if not quiet:
                if (symbol, candle_close) not in already_logged:    # never log the same candle twice
                    m.update(symbol=symbol, candle_close_utc=candle_close)
                    hits.append(m)
            else:
                m.update(symbol=symbol, candle_close_utc=candle_close)
                hits.append(m)
        time.sleep(0.05)                                   # be gentle with Binance

    if not hits:
        if not quiet:
            print("  No signals this scan.\n")
        return {"paused": False, "btc_1h": btc_1h, "btc_4h": btc_4h, "signals": [], "coins_checked": len(pairs)}

    # Get the live price right now: this is the price you could actually have acted on.
    if not quiet:
        rows = read_rows()
        print(f"\n  {'COIN':<12}{'PRICE':>14}{'VOL x':>8}{'RSI':>7}{'COIN 4h':>10}{'BTC 4h':>9}{'vs BTC':>9}")

    for h in hits:
        h["live_price"] = float(get("/fapi/v1/ticker/price", {"symbol": h["symbol"]})["price"])
        if not quiet:
            print(f"  {h['symbol'].replace('USDT', ''):<12}{h['live_price']:>14.8g}{h['volume_ratio']:>8.2f}{h['rsi']:>7.1f}"
                  f"{h['change_4h']:>+9.2f}%{btc_4h:>+8.2f}%{h['change_4h'] - btc_4h:>+8.2f}%")
            rows.append({
                "detected_at_utc": utc_text(time.time()), "candle_close_utc": h["candle_close_utc"], "symbol": h["symbol"],
                "price_at_signal": h["live_price"], "volume_ratio": round(h["volume_ratio"], 2), "rsi": round(h["rsi"], 1),
                "change_4h_pct": round(h["change_4h"], 2), "btc_change_4h_pct": round(btc_4h, 2),
            })
        else:
            h["btc_4h"] = btc_4h

    if not quiet:
        write_rows(rows)
        print(f"  Saved {len(hits)} signal(s) to {CSV_FILE}\n")

    return {
        "paused": False,
        "btc_1h": btc_1h,
        "btc_4h": btc_4h,
        "signals": hits,
        "coins_checked": len(pairs)
    }


# =====================================================================================
# THE WIN-RATE REPORT
# =====================================================================================
def report():
    rows = read_rows()
    print(f"\nSignals logged: {len(rows)}")
    for m in FOLLOW_UP_MINUTES:
        vals = [float(r[f"pct_{m}m"]) for r in rows if r.get(f"pct_{m}m")]
        if not vals:
            print(f"After {m:>2} min: no finished measurements yet")
            continue
        up = sum(v > 0 for v in vals)
        up_fees = sum(v > ROUND_TRIP_FEE_PCT for v in vals)
        print(f"After {m:>2} min: {len(vals)} measured | up {100 * up / len(vals):.0f}% | "
              f"up after {ROUND_TRIP_FEE_PCT}% fees {100 * up_fees / len(vals):.0f}% | "
              f"average {sum(vals) / len(vals):+.2f}% | best {max(vals):+.2f}% | worst {min(vals):+.2f}%")
    print("\nTip: do not trust these numbers until you have at least 100 finished signals.\n")


# =====================================================================================
# START HERE
# =====================================================================================
def main():
    ap = argparse.ArgumentParser(description="Read-only Binance momentum scanner")
    ap.add_argument("--once", action="store_true", help="scan one time and exit")
    ap.add_argument("--report", action="store_true", help="show win-rate stats from signals.csv")
    args = ap.parse_args()

    if args.report:
        report()
        return
    print("Read-only scanner started. It cannot place orders. Press Ctrl+C to stop.\n")
    while True:
        started = time.time()
        try:
            run_scan()
        except KeyboardInterrupt:
            raise
        except Exception as e:                             # network trouble etc: report it and keep going
            print(f"Scan failed: {e}\n")
        if args.once:
            return
        time.sleep(max(5, LOOP_SECONDS - (time.time() - started)))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped.")
        sys.exit(0)

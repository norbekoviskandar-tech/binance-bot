#!/usr/bin/env python3
"""
Binance USDT-M Futures bot (EMA pullback strategy).

Modes:
  python bot.py --scan              show current signals, no orders, no keys needed
  python bot.py --backtest          replay rules on the last 90 days (public data)
  python bot.py                     run on TESTNET (fake money)  <- start here
  python bot.py --live              run on REAL money (asks you to type CONFIRM)

Create a file named STOP in this folder to close everything and exit.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import hmac
import json
import logging
import math
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlencode

import numpy as np
import pandas as pd
import requests
import yaml
from dotenv import load_dotenv

LIVE_URL = "https://fapi.binance.com"
TEST_URL = "https://testnet.binancefuture.com"
STABLES = {"USDC", "FDUSD", "TUSD", "BUSD", "USDP", "DAI", "USDE"}
STATE_FILE = Path("state.json")
TRADES_FILE = Path("trades.csv")
KILL_FILE = Path("STOP")
MAX_SIGNAL_AGE_MIN = 20  # only act on a signal candle that closed recently
BE_BUFFER = 0.0008       # breakeven stop sits 0.08% past entry to cover fees
MIN_4H_CHANGE_PCT = 1.0  # minimum 4h change % to trade (only strong gainers)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.FileHandler("bot.log"), logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("bot")


# --------------------------------------------------------------------------- config
@dataclass
class Cfg:
    equity: float = 30.0
    risk_per_trade_usd: float = 1.0
    max_leverage: int = 3
    daily_loss_limit_usd: float = 3.0
    universe_size: int = 20
    taker_fee: float = 0.0004
    funding_limit: float = 0.0005
    stop_atr_mult: float = 1.5
    tp1_r: float = 2.0
    tp2_r: float = 3.0
    scan_interval_min: int = 15


def load_cfg(path: str) -> Cfg:
    d = yaml.safe_load(open(path)) or {}
    return Cfg(**{k: v for k, v in d.items() if k in Cfg.__dataclass_fields__})


# --------------------------------------------------------------------------- API client
class BinanceError(Exception):
    def __init__(self, status: int, code: int, msg: str):
        super().__init__(f"HTTP {status} code {code}: {msg}")
        self.status, self.code, self.msg = status, code, msg


class Client:
    def __init__(self, base: str, key: str = "", secret: str = ""):
        self.base, self.secret, self.offset = base, secret.encode(), 0
        self.s = requests.Session()
        if key:
            self.s.headers["X-MBX-APIKEY"] = key

    def sync_time(self) -> None:
        t = self.s.get(self.base + "/fapi/v1/time", timeout=10).json()["serverTime"]
        self.offset = t - int(time.time() * 1000)

    def request(self, method: str, path: str, params: Optional[dict] = None,
                signed: bool = False, retries: int = 4) -> Any:
        params = {k: v for k, v in (params or {}).items() if v is not None}
        last: Any = None
        for attempt in range(retries):
            p = dict(params)
            if signed:
                p["timestamp"] = int(time.time() * 1000) + self.offset
                p["recvWindow"] = 10000
            q = urlencode(p)
            if signed:
                q += "&signature=" + hmac.new(self.secret, q.encode(), hashlib.sha256).hexdigest()
            try:
                r = self.s.request(method, f"{self.base}{path}?{q}", timeout=15)
            except requests.RequestException as e:
                last = e
                log.warning("network error %s %s: %s", method, path, e)
                time.sleep(2 ** attempt)
                continue
            if r.status_code == 200:
                return r.json()
            try:
                body = r.json()
            except ValueError:
                body = {}
            code, msg = body.get("code", 0), body.get("msg", r.text[:200])
            if r.status_code in (418, 429):
                wait = int(r.headers.get("Retry-After", 2 ** (attempt + 2)))
                log.warning("rate limited, sleeping %ss", wait)
                time.sleep(wait)
                last = BinanceError(r.status_code, code, msg)
                continue
            if code == -1021:  # clock drift
                self.sync_time()
                continue
            if r.status_code >= 500:
                last = BinanceError(r.status_code, code, msg)
                time.sleep(2 ** attempt)
                continue
            raise BinanceError(r.status_code, code, msg)
        raise RuntimeError(f"{method} {path} failed after {retries} tries: {last}")


# --------------------------------------------------------------------------- indicators
def klines_df(raw: list) -> pd.DataFrame:
    cols = ["open_time", "open", "high", "low", "close", "volume", "close_time",
            "qv", "trades", "tbb", "tbq", "ignore"]
    df = pd.DataFrame(raw, columns=cols)
    for c in ("open", "high", "low", "close", "volume"):
        df[c] = df[c].astype(float)
    df["open_time"] = df["open_time"].astype("int64")
    df["close_time"] = df["close_time"].astype("int64")
    return df


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    d = close.diff()
    up, dn = d.clip(lower=0), -d.clip(upper=0)
    ru, rd = up.ewm(alpha=1 / n, adjust=False).mean(), dn.ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + ru / rd.replace(0, np.nan))


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    pc = df["close"].shift()
    tr = pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def add_ind(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["ema20"], df["ema50"] = ema(df["close"], 20), ema(df["close"], 50)
    df["rsi"], df["atr"] = rsi(df["close"]), atr(df)
    df["vol_ma"] = df["volume"].rolling(20).mean().shift(1)  # previous 20 bars
    return df


def closed_only(df: pd.DataFrame) -> pd.DataFrame:
    return df[df["close_time"] < int(time.time() * 1000)].reset_index(drop=True)


def check_entry(bias: float, low: float, high: float, close: float,
                ema20: float, rsi_v: float, vol: float, vol_ma: float) -> int:
    """+1 long, -1 short, 0 none. Evaluated on a CLOSED 1h candle."""
    if any(math.isnan(x) for x in (ema20, rsi_v, vol_ma)) or vol <= vol_ma:
        return 0
    if bias > 0 and low <= ema20 < close and 40 <= rsi_v <= 55:
        return 1
    if bias < 0 and high >= ema20 > close and 45 <= rsi_v <= 60:
        return -1
    return 0


# --------------------------------------------------------------------------- symbols / sizing
@dataclass
class SymInfo:
    symbol: str
    tick: Decimal
    step: Decimal
    min_qty: Decimal
    min_notional: Decimal


def D(x: Any) -> Decimal:
    return Decimal(str(x))


def floor_to(x: Any, step: Decimal) -> Decimal:
    return (D(x) / step).to_integral_value(ROUND_DOWN) * step


def round_to(x: Any, step: Decimal) -> Decimal:
    return (D(x) / step).to_integral_value(ROUND_HALF_UP) * step


def fmt(d: Decimal) -> str:
    return format(d, "f")


def load_symbols(c: Client) -> dict[str, SymInfo]:
    out = {}
    for s in c.request("GET", "/fapi/v1/exchangeInfo")["symbols"]:
        if s["status"] != "TRADING" or s["contractType"] != "PERPETUAL" or s["quoteAsset"] != "USDT":
            continue
        if s["baseAsset"] in STABLES:
            continue
        f = {x["filterType"]: x for x in s["filters"]}
        mn = f.get("MIN_NOTIONAL", {}).get("notional", "5")
        out[s["symbol"]] = SymInfo(s["symbol"], D(f["PRICE_FILTER"]["tickSize"]), D(f["LOT_SIZE"]["stepSize"]),
                                   D(f["LOT_SIZE"]["minQty"]), D(mn))
    return out


def get_universe(c: Client, infos: dict[str, SymInfo], n: int) -> list[str]:
    t = [x for x in c.request("GET", "/fapi/v1/ticker/24hr") if x["symbol"] in infos]
    t.sort(key=lambda x: float(x["priceChangePercent"]), reverse=True)  # Sort by gainers
    return [x["symbol"] for x in t[:n]]


def size_position(info: SymInfo, price: float, stop_dist: float, cfg: Cfg, equity: float):
    """Returns (qty, leverage, "") or (None, None, reason)."""
    if stop_dist <= 0:
        return None, None, "bad stop"
    qty = floor_to(cfg.risk_per_trade_usd / stop_dist, info.step)
    if qty < info.min_qty:
        return None, None, f"min qty {info.min_qty} would risk more than ${cfg.risk_per_trade_usd}"
    notional = float(qty) * price
    if notional < float(info.min_notional) * 1.05:
        return None, None, f"notional ${notional:.2f} below minimum ${info.min_notional}"
    lev = max(1, math.ceil(notional / (equity * 0.9)))
    if lev > cfg.max_leverage:
        return None, None, f"needs {lev}x leverage (max {cfg.max_leverage}x): too big for account"
    if 1 / lev < 2 * stop_dist / price:
        return None, None, "liquidation too close to stop"
    return qty, lev, ""


# --------------------------------------------------------------------------- scanning
def scan(c: Client, symbols: list[str], infos: dict[str, SymInfo], cfg: Cfg, equity: float,
         traded: set, max_age_min: float = MAX_SIGNAL_AGE_MIN, verbose: bool = False) -> list[dict]:
    out = []
    for s in symbols:
        try:
            # SCALPING MODE: 15m bias, 5m signals
            d4 = add_ind(closed_only(klines_df(c.request("GET", "/fapi/v1/klines", {"symbol": s, "interval": "15m", "limit": 120}))))
            d1 = add_ind(closed_only(klines_df(c.request("GET", "/fapi/v1/klines", {"symbol": s, "interval": "5m", "limit": 120}))))
        except Exception as e:  # noqa: BLE001
            log.warning("data fetch failed for %s: %s", s, e)
            continue
        if len(d4) < 60 or len(d1) < 60:
            continue
        bias = float(np.sign(d4["ema20"].iloc[-1] - d4["ema50"].iloc[-1]))
        r = d1.iloc[-1]
        # Check 4h change on bias timeframe (15m candles for scalping)
        change_4h = (d4["close"].iloc[-1] / d4["close"].iloc[-16] - 1) * 100 if len(d4) >= 16 else 0
        if change_4h < MIN_4H_CHANGE_PCT:
            if verbose:
                log.info("%s skipped: 4h change %.2f%% below minimum %.1f%%", s, change_4h, MIN_4H_CHANGE_PCT)
            continue
        age = (time.time() * 1000 - r["close_time"]) / 60000
        side = check_entry(bias, r["low"], r["high"], r["close"], r["ema20"], r["rsi"], r["volume"], r["vol_ma"])
        if not side or age > max_age_min or (s, int(r["close_time"])) in traded:
            continue
        fr = float(c.request("GET", "/fapi/v1/premiumIndex", {"symbol": s})["lastFundingRate"])
        if (side > 0 and fr > cfg.funding_limit) or (side < 0 and fr < -cfg.funding_limit):
            if verbose:
                log.info("%s skipped: funding %.4f%% against position", s, fr * 100)
            continue
        sd = cfg.stop_atr_mult * float(r["atr"])
        qty, lev, why = size_position(infos[s], float(r["close"]), sd, cfg, equity)
        if qty is None:
            if verbose or True:
                log.info("%s %s signal skipped: %s", s, "LONG" if side > 0 else "SHORT", why)
            continue
        out.append(dict(symbol=s, side=side, price=float(r["close"]), stop_dist=sd, qty=qty, lev=lev,
                        vol_ratio=float(r["volume"] / r["vol_ma"]), candle=int(r["close_time"]), funding=fr))
    out.sort(key=lambda x: -x["vol_ratio"])
    return out


# --------------------------------------------------------------------------- account helpers
def _first_ok(c: Client, paths: tuple[str, ...], params: Optional[dict] = None):
    err = None
    for p in paths:
        try:
            return c.request("GET", p, params, signed=True)
        except BinanceError as e:
            err = e
    raise err  # type: ignore[misc]


def get_positions(c: Client) -> dict[str, tuple[float, float]]:
    r = _first_ok(c, ("/fapi/v3/positionRisk", "/fapi/v2/positionRisk"))
    return {p["symbol"]: (float(p["positionAmt"]), float(p["entryPrice"])) for p in r if float(p["positionAmt"]) != 0}


def get_available_usdt(c: Client) -> float:
    for a in _first_ok(c, ("/fapi/v3/balance", "/fapi/v2/balance")):
        if a["asset"] == "USDT":
            return float(a["availableBalance"])
    return 0.0


def daily_pnl(c: Client) -> float:
    start = int(datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).timestamp() * 1000)
    inc = c.request("GET", "/fapi/v1/income", {"startTime": start, "limit": 1000}, signed=True)
    return sum(float(i["income"]) for i in inc if i["incomeType"] in ("REALIZED_PNL", "COMMISSION", "FUNDING_FEE"))


def open_algo(c: Client, symbol: str) -> list[dict]:
    try:
        return c.request("GET", "/fapi/v1/openAlgoOrders", {"symbol": symbol}, signed=True) or []
    except BinanceError as e:
        log.error("openAlgoOrders failed: %s", e)
        return []


def algo_order(c: Client, symbol: str, side: str, typ: str, trigger: Decimal, qty: Optional[Decimal] = None) -> int:
    """Conditional order via the Algo Order endpoint (required since Binance's Dec 2025 migration)."""
    p = {"algoType": "CONDITIONAL", "symbol": symbol, "side": side, "type": typ,
         "triggerPrice": fmt(trigger), "workingType": "MARK_PRICE"}
    if qty is None:
        p["closePosition"] = "true"
    else:
        p["quantity"], p["reduceOnly"] = fmt(qty), "true"
    return int(c.request("POST", "/fapi/v1/algoOrder", p, signed=True)["algoId"])


def cancel_all(c: Client, symbol: str) -> None:
    for path in ("/fapi/v1/allOpenOrders", "/fapi/v1/algoOpenOrders"):
        try:
            c.request("DELETE", path, {"symbol": symbol}, signed=True)
        except Exception as e:  # noqa: BLE001
            log.warning("cancel %s failed: %s", path, e)


def close_position(c: Client, symbol: str, amt: float) -> None:
    side = "SELL" if amt > 0 else "BUY"
    c.request("POST", "/fapi/v1/order", {"symbol": symbol, "side": side, "type": "MARKET",
                                          "quantity": fmt(D(abs(amt))), "reduceOnly": "true"}, signed=True)


def log_trade(event: str, symbol: str, side: Any, qty: Any, price: Any, note: str = "") -> None:
    new = not TRADES_FILE.exists()
    with TRADES_FILE.open("a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["time_utc", "event", "symbol", "side", "qty", "price", "note"])
        w.writerow([datetime.now(timezone.utc).isoformat(timespec="seconds"), event, symbol, side, qty, price, note])


# --------------------------------------------------------------------------- the bot
class Bot:
    def __init__(self, client: Client, cfg: Cfg):
        self.c, self.cfg = client, cfg
        self.infos = load_symbols(client)
        self.universe: list[str] = []
        self.universe_ts = 0.0
        self.state: Optional[dict] = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else None
        self.traded: set = set()
        self.last_bucket: Optional[int] = None
        self.halted = False

    def save_state(self) -> None:
        if self.state:
            STATE_FILE.write_text(json.dumps(self.state))
        elif STATE_FILE.exists():
            STATE_FILE.unlink()

    # ---- entry
    def enter(self, sig: dict) -> None:
        c, cfg, s = self.c, self.cfg, sig["symbol"]
        info, side = self.infos[s], sig["side"]
        self.traded.add((s, sig["candle"]))
        for path, p in (("/fapi/v1/marginType", {"symbol": s, "marginType": "ISOLATED"}),
                        ("/fapi/v1/leverage", {"symbol": s, "leverage": sig["lev"]})):
            try:
                c.request("POST", path, p, signed=True)
            except BinanceError as e:
                if e.code != -4046:  # -4046 = margin type already isolated
                    log.error("%s failed for %s: %s: not entering", path, s, e)
                    return
        try:
            c.request("POST", "/fapi/v1/order", {"symbol": s, "side": "BUY" if side > 0 else "SELL", "type": "MARKET",
                                                  "quantity": fmt(sig["qty"])}, signed=True, retries=1)
        except Exception as e:  # noqa: BLE001
            log.error("entry order error for %s: %s (checking whether it filled)", s, e)
        time.sleep(1.5)
        pos = get_positions(c)
        if s not in pos:
            log.warning("no position after entry attempt on %s", s)
            return
        amt, entry = pos[s]
        log_trade("ENTRY", s, "LONG" if side > 0 else "SHORT", amt, entry, f"lev {sig['lev']}x")
        sd, tick = sig["stop_dist"], info.tick
        exit_side = "SELL" if side > 0 else "BUY"
        sl = round_to(entry - side * sd, tick)
        # 1) stop-loss first. If it cannot be placed and confirmed, flatten immediately.
        try:
            sl_id = algo_order(c, s, exit_side, "STOP_MARKET", sl)
            time.sleep(0.5)
            if not any(int(o.get("algoId", -1)) == sl_id for o in open_algo(c, s)):
                raise RuntimeError("stop not found in open algo orders")
        except Exception as e:  # noqa: BLE001
            log.critical("STOP-LOSS FAILED on %s (%s): closing position now", s, e)
            cancel_all(c, s)
            try:
                close_position(c, s, amt)
                log_trade("EMERGENCY_CLOSE", s, "", amt, "", "stop-loss could not be placed")
            except Exception as e2:  # noqa: BLE001
                log.critical("EMERGENCY CLOSE FAILED, CLOSE %s MANUALLY ON BINANCE: %s", s, e2)
            return
        # 2) take-profits (failure here is non-fatal: the stop already protects the trade)
        qa = D(abs(amt))
        half = floor_to(qa / 2, info.step)
        split = half >= info.min_qty and half * D(entry) >= info.min_notional and (qa - half) >= info.min_qty
        try:
            if split:
                algo_order(c, s, exit_side, "TAKE_PROFIT_MARKET", round_to(entry + side * cfg.tp1_r * sd, tick), half)
                algo_order(c, s, exit_side, "TAKE_PROFIT_MARKET", round_to(entry + side * cfg.tp2_r * sd, tick))
            else:
                algo_order(c, s, exit_side, "TAKE_PROFIT_MARKET", round_to(entry + side * cfg.tp1_r * sd, tick))
        except Exception as e:  # noqa: BLE001
            log.error("take-profit placement failed on %s (stop is active): %s", s, e)
        self.state = dict(symbol=s, side=side, entry=entry, qty0=abs(amt), sl_id=sl_id, be_done=False, split=split)
        self.save_state()
        log.info("OPEN %s %s qty=%s entry=%s SL=%s", s, "LONG" if side > 0 else "SHORT", amt, entry, sl)

    # ---- manage open trade
    def manage(self, pos: dict) -> None:
        st = self.state
        assert st
        s = st["symbol"]
        amt, entry = pos.get(s, (0.0, 0.0))
        if amt == 0:
            cancel_all(self.c, s)
            log_trade("EXIT", s, "", "", "", f"flat; day pnl {daily_pnl(self.c):.2f} USDT")
            log.info("%s closed. Day PnL %.2f USDT", s, daily_pnl(self.c))
            self.state = None
            self.save_state()
            return
        if st["split"] and not st["be_done"] and abs(amt) <= st["qty0"] * 0.6:
            info = self.infos[s]
            be = round_to(st["entry"] * (1 + st["side"] * BE_BUFFER), info.tick)
            try:  # place the new stop first, then cancel the old one: never unprotected
                new_id = algo_order(self.c, s, "SELL" if st["side"] > 0 else "BUY", "STOP_MARKET", be)
                self.c.request("DELETE", "/fapi/v1/algoOrder", {"algoId": st["sl_id"]}, signed=True)
                st.update(sl_id=new_id, be_done=True)
                self.save_state()
                log_trade("BREAKEVEN", s, "", "", fmt(be), "stop moved after TP1")
                log.info("%s stop moved to breakeven %s", s, be)
            except Exception as e:  # noqa: BLE001
                log.error("breakeven move failed on %s (old stop still active): %s", s, e)

    # ---- one loop iteration
    def tick(self) -> None:
        c, cfg = self.c, self.cfg
        pos = get_positions(c)
        if self.state:
            self.manage(pos)
            return
        if pos:  # position we have no record of
            s = next(iter(pos))
            if not open_algo(c, s):
                log.critical("unprotected position on %s: closing it", s)
                close_position(c, s, pos[s][0])
            return
        pnl = daily_pnl(c)
        if pnl <= -cfg.daily_loss_limit_usd:
            if not self.halted:
                log.warning("daily loss limit hit (%.2f USDT). No new trades until next UTC day.", pnl)
            self.halted = True
            return
        self.halted = False
        period = cfg.scan_interval_min * 60
        bucket = int(time.time() // period)
        if bucket == self.last_bucket or time.time() % period < 10:  # wait 10s so candles are final
            return
        self.last_bucket = bucket
        if time.time() - self.universe_ts > 6 * 3600 or not self.universe:
            self.universe = get_universe(c, self.infos, cfg.universe_size)
            self.universe_ts = time.time()
        equity = min(cfg.equity, get_available_usdt(c))
        sigs = scan(c, self.universe, self.infos, cfg, equity, self.traded)
        log.info("scan done: %d tradable signal(s), equity used %.2f", len(sigs), equity)
        if sigs:
            self.enter(sigs[0])

    def kill(self) -> None:
        log.warning("STOP file found: closing everything")
        for s, (amt, _) in get_positions(self.c).items():
            cancel_all(self.c, s)
            close_position(self.c, s, amt)
            log_trade("KILL_CLOSE", s, "", amt, "", "")
        self.state = None
        self.save_state()

    def run(self) -> None:
        try:  # one-way mode required
            self.c.request("POST", "/fapi/v1/positionSide/dual", {"dualSidePosition": "false"}, signed=True)
        except BinanceError as e:
            if e.code != -4059:
                log.error("could not set one-way position mode: %s", e)
        log.info("bot running. Create a file named STOP to shut down.")
        while True:
            if KILL_FILE.exists():
                self.kill()
                KILL_FILE.unlink()
                return
            try:
                self.tick()
            except Exception:  # noqa: BLE001
                log.exception("tick failed; will retry")
            time.sleep(30)


# --------------------------------------------------------------------------- backtest
def hist_klines(c: Client, symbol: str, interval: str, days: int) -> pd.DataFrame:
    end = int(time.time() * 1000)
    start, out = end - days * 86_400_000, []
    while start < end:
        r = c.request("GET", "/fapi/v1/klines", {"symbol": symbol, "interval": interval, "startTime": start, "limit": 1500})
        if not r:
            break
        out += r
        start = r[-1][6] + 1
        if len(r) < 1500:
            break
    return closed_only(klines_df(out))


def hist_funding(c: Client, symbol: str, days: int) -> list[dict]:
    start = int((time.time() - days * 86400) * 1000)
    return c.request("GET", "/fapi/v1/fundingRate", {"symbol": symbol, "startTime": start, "limit": 1000}) or []


def sim_symbol(sym: str, d1: pd.DataFrame, d4: pd.DataFrame, fund: list[dict], info: SymInfo, cfg: Cfg, days: int) -> list[dict]:
    o, h, l, c_ = (d1[x].values for x in ("open", "high", "low", "close"))
    ct, ot = d1["close_time"].values, d1["open_time"].values
    ema20, rsi_v, atr_v, vol, vma = (d1[x].values for x in ("ema20", "rsi", "atr", "volume", "vol_ma"))
    idx4 = np.searchsorted(d4["close_time"].values, ct, side="right") - 1
    b4 = np.sign((d4["ema20"] - d4["ema50"]).values)
    ft = np.array([x["fundingTime"] for x in fund])
    fr = np.array([float(x["fundingRate"]) for x in fund])
    start_ms = ct[-1] - days * 86_400_000
    n, i, trades = len(d1), 20, []  # Reduced warmup for scalping
    while i < n - 1:
        if ct[i] < start_ms or idx4[i] < 10:  # Reduced warmup for scalping
            i += 1
            continue
        side = check_entry(b4[idx4[i]], l[i], h[i], c_[i], ema20[i], rsi_v[i], vol[i], vma[i])
        if not side:
            i += 1
            continue
        k = np.searchsorted(ft, ct[i], side="right") - 1
        rate = fr[k] if k >= 0 else 0.0
        if (side > 0 and rate > cfg.funding_limit) or (side < 0 and rate < -cfg.funding_limit):
            i += 1
            continue
        sd = cfg.stop_atr_mult * atr_v[i]
        qty, _, _ = size_position(info, c_[i], sd, cfg, cfg.equity)
        if qty is None:
            i += 1
            continue
        q, e = float(qty), o[i + 1]                       # enter at next bar's open
        sl, tp1, tp2 = e - side * sd, e + side * cfg.tp1_r * sd, e + side * cfg.tp2_r * sd
        half = floor_to(q / 2, info.step)
        split = half >= info.min_qty and half * D(e) >= info.min_notional and (D(q) - half) >= info.min_qty
        rem, realized, fees, tp1_hit = q, 0.0, cfg.taker_fee * q * e, False
        seg_start, segs, exit_k = ot[i + 1], [], None
        for j in range(i + 1, n):
            if (l[j] <= sl) if side > 0 else (h[j] >= sl):  # stop checked first = pessimistic
                realized += rem * (sl - e) * side
                fees += cfg.taker_fee * rem * sl
                segs.append((seg_start, ct[j], rem))
                rem, exit_k = 0.0, j
                break
            if not tp1_hit and ((h[j] >= tp1) if side > 0 else (l[j] <= tp1)):
                part = float(half) if split else rem
                realized += part * (tp1 - e) * side
                fees += cfg.taker_fee * part * tp1
                segs.append((seg_start, ct[j], rem))
                seg_start, rem, tp1_hit = ct[j], rem - part, True
                if rem <= 1e-12:
                    exit_k = j
                    break
                sl = e * (1 + side * BE_BUFFER)
                continue
            if tp1_hit and ((h[j] >= tp2) if side > 0 else (l[j] <= tp2)):
                realized += rem * (tp2 - e) * side
                fees += cfg.taker_fee * rem * tp2
                segs.append((seg_start, ct[j], rem))
                rem, exit_k = 0.0, j
                break
        if exit_k is None:  # data ended: close at last price
            exit_k = n - 1
            realized += rem * (c_[-1] - e) * side
            fees += cfg.taker_fee * rem * c_[-1]
            segs.append((seg_start, ct[-1], rem))
        fund_cost = sum(side * r_ * qq * e for (t0, t1, qq) in segs for tt, r_ in zip(ft, fr) if t0 < tt <= t1)
        trades.append(dict(symbol=sym, side=side, entry_t=int(ot[i + 1]), exit_t=int(ct[exit_k]), risk=q * sd,
                           gross=realized, fees=fees, funding=fund_cost, pnl=realized - fees - fund_cost))
        i = exit_k + 1
    return trades


def backtest(cfg: Cfg, days: int) -> None:
    pub = Client(LIVE_URL)
    infos = load_symbols(pub)
    syms = get_universe(pub, infos, cfg.universe_size)
    cands: list[dict] = []
    for s in syms:
        try:
            # SCALPING MODE: 5m signals, 15m bias
            d1 = add_ind(hist_klines(pub, s, "5m", days + 1))
            d4 = add_ind(hist_klines(pub, s, "15m", days + 3))
            cands += sim_symbol(s, d1, d4, hist_funding(pub, s, days + 1), infos[s], cfg, days)
            log.info("backtested %s", s)
        except Exception as e:  # noqa: BLE001
            log.warning("skip %s: %s", s, e)
    cands.sort(key=lambda t: t["entry_t"])
    taken, free_at = [], 0
    for t in cands:  # one position at a time, like the live bot
        if t["entry_t"] >= free_at:
            taken.append(t)
            free_at = t["exit_t"]
    if not taken:
        print("No trades. The rules never fired (or nothing was affordable at this account size).")
        return
    pnl = np.array([t["pnl"] for t in taken])
    r = np.array([t["pnl"] / t["risk"] for t in taken])
    cum = np.cumsum(pnl)
    dd = float((np.maximum.accumulate(np.maximum(cum, 0)) - cum).max())
    wins, losses = pnl[pnl > 0].sum(), -pnl[pnl < 0].sum()
    print(f"\n=== BACKTEST: last {days} days, {len(syms)} symbols, start equity ${cfg.equity:.0f}, risk ${cfg.risk_per_trade_usd}/trade ===")
    print(f"Trades:           {len(taken)}")
    print(f"Win rate:         {100 * (pnl > 0).mean():.1f}%")
    print(f"Average R:        {r.mean():+.2f}")
    print(f"Net PnL:          {pnl.sum():+.2f} USDT")
    print(f"Fees paid:        {sum(t['fees'] for t in taken):.2f} USDT")
    print(f"Funding paid:     {sum(t['funding'] for t in taken):+.2f} USDT (negative = received)")
    print(f"Max drawdown:     {dd:.2f} USDT")
    print(f"Profit factor:    {wins / losses:.2f}" if losses > 0 else "Profit factor:    n/a (no losing trades)")
    pd.DataFrame(taken).to_csv("backtest_trades.csv", index=False)
    print("\nTrade list saved to backtest_trades.csv")
    print("Caveats: universe = TODAY's top pairs (survivorship bias); stop assumed hit before TP when both occur in one candle;")
    print("no slippage; one position at a time. A positive result is not a promise: past data is not the future.")


# --------------------------------------------------------------------------- entry point
def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--live", action="store_true", help="trade with REAL money (default is testnet)")
    ap.add_argument("--backtest", action="store_true")
    ap.add_argument("--scan", action="store_true", help="print current signals only; no orders")
    ap.add_argument("--days", type=int, default=90)
    ap.add_argument("--config", default="config.yaml")
    a = ap.parse_args()
    load_dotenv()
    cfg = load_cfg(a.config)

    if a.backtest:
        backtest(cfg, a.days)
        return
    if a.scan:
        pub = Client(LIVE_URL)
        infos = load_symbols(pub)
        sigs = scan(pub, get_universe(pub, infos, cfg.universe_size), infos, cfg, cfg.equity, set(), max_age_min=5, verbose=True)
        for s in sigs:
            print(f"{s['symbol']:<14}{'LONG' if s['side'] > 0 else 'SHORT':<6} price={s['price']} qty={s['qty']} "
                  f"lev={s['lev']}x stop_dist={s['stop_dist']:.6g} funding={s['funding'] * 100:.4f}%")
        if not sigs:
            print("No affordable signals right now. That is normal: most of the time the answer is 'no trade'.")
        return

    prefix = "BINANCE_LIVE" if a.live else "BINANCE_TESTNET"
    key, secret = os.getenv(f"{prefix}_API_KEY", ""), os.getenv(f"{prefix}_API_SECRET", "")
    if not key or not secret:
        sys.exit(f"Missing {prefix}_API_KEY / {prefix}_API_SECRET in your .env file.")
    if a.live:
        print(f"LIVE MODE: real money, equity cap ${cfg.equity}, risk ${cfg.risk_per_trade_usd}/trade, daily stop ${cfg.daily_loss_limit_usd}.")
        if input("Type CONFIRM to continue: ").strip() != "CONFIRM":
            sys.exit("Cancelled.")
    client = Client(LIVE_URL if a.live else TEST_URL, key, secret)
    client.sync_time()
    log.info("mode: %s", "LIVE" if a.live else "TESTNET")
    Bot(client, cfg).run()


if __name__ == "__main__":
    main()

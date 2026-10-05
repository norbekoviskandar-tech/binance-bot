#!/usr/bin/env python3
"""
Autonomous Binance Futures Trading Bot.
Reads signals from scanner.py and executes trades with strict safety limits.
"""
import csv
import hashlib
import hmac
import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlencode

import pandas as pd
import requests
import yaml
from dotenv import load_dotenv

# Import scanner for signal logic
import scanner

# Load environment variables
load_dotenv()

# =====================================================================================
# CONFIGURATION
# =====================================================================================
CONFIG_FILE = Path("config.yaml")
BOT_LOG = Path("bot_log.csv")
TRADES_LOG = Path("trades_log.csv")
STATE_FILE = Path("autobot_state.json")
STOP_FILE = Path("STOP")

# Binance URLs
LIVE_URL = "https://fapi.binance.com"
TESTNET_URL = "https://testnet.binancefuture.com"

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.FileHandler("autobot.log"), logging.StreamHandler()],
)
log = logging.getLogger("autobot")


# =====================================================================================
# CONFIG CLASS
# =====================================================================================
@dataclass
class BotConfig:
    risk_per_trade_usd: float = 0.27
    max_leverage: int = 3
    max_open_positions: int = 1
    max_daily_loss_pct: float = 3.0
    max_weekly_loss_pct: float = 8.0
    require_backtest_pass: bool = True
    use_testnet: bool = False
    universe_size: int = 30
    funding_limit: float = 0.0005
    stop_atr_mult: float = 1.5
    tp1_r: float = 2.0
    tp2_r: float = 3.0
    min_score: int = 55
    
    @classmethod
    def from_yaml(cls, path: Path) -> "BotConfig":
        if not path.exists():
            return cls()
        with path.open() as f:
            data = yaml.safe_load(f) or {}
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


# =====================================================================================
# BINANCE CLIENT
# =====================================================================================
class BinanceClient:
    def __init__(self, base_url: str, api_key: str = "", api_secret: str = ""):
        self.base = base_url
        self.key = api_key
        self.secret = api_secret.encode() if api_secret else b""
        self.session = requests.Session()
        self.offset = 0
        
    def sync_time(self) -> None:
        """Sync local time with Binance server time."""
        t = self.session.get(f"{self.base}/fapi/v1/time", timeout=10).json()["serverTime"]
        self.offset = t - int(time.time() * 1000)
    
    def _sign(self, params: Dict) -> str:
        """Sign request parameters."""
        if not self.secret:
            return ""
        q = urlencode(params)
        return hmac.new(self.secret, q.encode(), hashlib.sha256).hexdigest()
    
    def request(
        self,
        method: str,
        path: str,
        params: Optional[Dict] = None,
        signed: bool = False,
        retries: int = 3
    ) -> Any:
        """Make API request with retries."""
        params = params or {}
        last_error = None
        
        for attempt in range(retries):
            try:
                if signed:
                    params["timestamp"] = int(time.time() * 1000) + self.offset
                    params["signature"] = self._sign(params)
                
                headers = {}
                if self.key:
                    headers["X-MBX-APIKEY"] = self.key
                
                r = self.session.request(
                    method,
                    f"{self.base}{path}",
                    params=params,
                    headers=headers,
                    timeout=15
                )
                
                if r.status_code == 200:
                    return r.json()
                
                body = r.json() if r.content else {}
                code = body.get("code", 0)
                msg = body.get("msg", r.text[:200])
                
                if r.status_code in (418, 429):
                    wait = int(r.headers.get("Retry-After", 5))
                    log.warning(f"Rate limited, waiting {wait}s")
                    time.sleep(wait)
                    continue
                
                if code == -1021:  # Clock drift
                    self.sync_time()
                    continue
                
                raise Exception(f"HTTP {r.status_code} code {code}: {msg}")
                
            except requests.RequestException as e:
                last_error = e
                log.warning(f"Network error: {e}")
                time.sleep(2 ** attempt)
                continue
        
        raise Exception(f"Request failed after {retries} attempts: {last_error}")
    
    # Public endpoints (no key needed)
    def get_ticker_24h(self, symbol: Optional[str] = None) -> List[Dict]:
        """Get 24h ticker data."""
        params = {"symbol": symbol} if symbol else {}
        return self.request("GET", "/fapi/v1/ticker/24hr", params=params)
    
    def get_klines(self, symbol: str, interval: str, limit: int = 500) -> List:
        """Get kline data."""
        return self.request("GET", "/fapi/v1/klines", params={
            "symbol": symbol,
            "interval": interval,
            "limit": limit
        })
    
    def get_funding_rate(self, symbol: str) -> Dict:
        """Get funding rate."""
        return self.request("GET", "/fapi/v1/premiumIndex", params={"symbol": symbol})
    
    def get_exchange_info(self) -> Dict:
        """Get exchange info."""
        return self.request("GET", "/fapi/v1/exchangeInfo")
    
    # Private endpoints (require key)
    def get_account(self) -> Dict:
        """Get account information."""
        return self.request("GET", "/fapi/v2/account", signed=True)
    
    def get_positions(self) -> List[Dict]:
        """Get open positions."""
        return self.request("GET", "/fapi/v2/positionRisk", signed=True)
    
    def get_open_orders(self, symbol: Optional[str] = None) -> List[Dict]:
        """Get open orders."""
        params = {"symbol": symbol} if symbol else {}
        return self.request("GET", "/fapi/v1/openOrders", params=params, signed=True)
    
    def place_order(
        self,
        symbol: str,
        side: str,
        order_type: str,
        quantity: float,
        price: Optional[float] = None,
        reduce_only: bool = False,
        close_position: bool = False,
        time_in_force: str = "GTC"
    ) -> Dict:
        """Place an order."""
        params = {
            "symbol": symbol,
            "side": side,
            "type": order_type,
            "quantity": quantity,
            "reduceOnly": reduce_only,
            "closePosition": close_position,
            "timeInForce": time_in_force
        }
        if price is not None:
            params["price"] = price
        
        return self.request("POST", "/fapi/v1/order", params=params, signed=True)
    
    def cancel_all_orders(self, symbol: str) -> Dict:
        """Cancel all orders for a symbol."""
        return self.request("DELETE", "/fapi/v1/allOpenOrders", params={"symbol": symbol}, signed=True)
    
    def set_leverage(self, symbol: str, leverage: int) -> Dict:
        """Set leverage for a symbol."""
        return self.request("POST", "/fapi/v1/leverage", params={
            "symbol": symbol,
            "leverage": leverage
        }, signed=True)
    
    def set_margin_type(self, symbol: str, margin_type: str = "ISOLATED") -> Dict:
        """Set margin type."""
        return self.request("POST", "/fapi/v1/marginType", params={
            "symbol": symbol,
            "marginType": margin_type
        }, signed=True)


# =====================================================================================
# SYMBOL INFO
# =====================================================================================
def load_symbol_filters(client: BinanceClient) -> Dict[str, Dict]:
    """Load symbol trading rules from exchange."""
    info = client.get_exchange_info()
    filters = {}
    
    for s in info["symbols"]:
        if s["status"] != "TRADING" or s["contractType"] != "PERPETUAL":
            continue
        if s["quoteAsset"] != "USDT":
            continue
        
        f = {x["filterType"]: x for x in s["filters"]}
        filters[s["symbol"]] = {
            "tick_size": Decimal(f["PRICE_FILTER"]["tickSize"]),
            "step_size": Decimal(f["LOT_SIZE"]["stepSize"]),
            "min_qty": Decimal(f["LOT_SIZE"]["minQty"]),
            "min_notional": Decimal(f.get("MIN_NOTIONAL", {}).get("notional", "5")),
        }
    
    return filters


# =====================================================================================
# UNIVERSE
# =====================================================================================
def get_universe(client: BinanceClient, size: int = 30) -> List[str]:
    """Get top USDT perpetuals by 24h volume."""
    tickers = client.get_ticker_24h()
    usdt_pairs = [t for t in tickers if t["symbol"].endswith("USDT")]
    usdt_pairs.sort(key=lambda x: float(x["quoteVolume"]), reverse=True)
    return [t["symbol"] for t in usdt_pairs[:size]]


# =====================================================================================
# POSITION SIZING
# =====================================================================================
def calculate_position_size(
    price: float,
    stop_distance: float,
    risk_usd: float,
    filters: Dict,
    equity: float,
    max_leverage: int
) -> Tuple[Optional[float], int, str]:
    """Calculate position size based on risk."""
    if stop_distance <= 0:
        return None, 0, "Invalid stop distance"
    
    # Calculate quantity based on risk
    qty = risk_usd / stop_distance
    
    # Round to step size
    step = filters["step_size"]
    qty_rounded = (Decimal(str(qty)) / step).to_integral_value(ROUND_DOWN) * step
    qty = float(qty_rounded)
    
    # Check minimum quantity
    if qty < float(filters["min_qty"]):
        return None, 0, f"Quantity {qty} below minimum {filters['min_qty']}"
    
    # Calculate notional
    notional = qty * price
    
    # Check minimum notional
    if notional < float(filters["min_notional"]):
        return None, 0, f"Notional ${notional:.2f} below minimum ${filters['min_notional']}"
    
    # Calculate required leverage
    leverage = max(1, int(notional / (equity * 0.9)))
    
    if leverage > max_leverage:
        return None, 0, f"Needs {leverage}x leverage (max {max_leverage}x)"
    
    return qty, leverage, ""


# =====================================================================================
# STATE MANAGEMENT
# =====================================================================================
class BotState:
    def __init__(self):
        self.auto_trading = False
        self.daily_pnl = 0.0
        self.weekly_pnl = 0.0
        self.losing_streak = 0
        self.last_scan_time = 0
        self.positions = {}  # symbol -> position info
        self.disabled_until = 0  # timestamp when trading re-enabled
    
    def load(self):
        if STATE_FILE.exists():
            with STATE_FILE.open() as f:
                data = json.load(f)
                self.auto_trading = data.get("auto_trading", False)
                self.daily_pnl = data.get("daily_pnl", 0.0)
                self.weekly_pnl = data.get("weekly_pnl", 0.0)
                self.losing_streak = data.get("losing_streak", 0)
                self.last_scan_time = data.get("last_scan_time", 0)
                self.positions = data.get("positions", {})
                self.disabled_until = data.get("disabled_until", 0)
    
    def save(self):
        with STATE_FILE.open("w") as f:
            json.dump({
                "auto_trading": self.auto_trading,
                "daily_pnl": self.daily_pnl,
                "weekly_pnl": self.weekly_pnl,
                "losing_streak": self.losing_streak,
                "last_scan_time": self.last_scan_time,
                "positions": self.positions,
                "disabled_until": self.disabled_until,
            }, f)


# =====================================================================================
# MAIN BOT CLASS
# =====================================================================================
class AutoBot:
    def __init__(self):
        self.config = BotConfig.from_yaml(CONFIG_FILE)
        self.base_url = TESTNET_URL if self.config.use_testnet else LIVE_URL
        
        # Get API keys (support both naming conventions)
        api_key = os.getenv("BINANCE_API_KEY") or os.getenv("BINANCE_LIVE_API_KEY", "")
        api_secret = os.getenv("BINANCE_API_SECRET") or os.getenv("BINANCE_LIVE_API_SECRET", "")
        
        self.client = BinanceClient(self.base_url, api_key, api_secret)
        self.state = BotState()
        self.state.load()
        
        # Sync time if we have keys
        if api_key:
            self.client.sync_time()
        
        self.symbol_filters = {}
    
    def get_equity(self) -> float:
        """Get available equity from exchange."""
        try:
            account = self.client.get_account()
            for bal in account["assets"]:
                if bal["asset"] == "USDT":
                    return float(bal["availableBalance"])
        except:
            pass
        return 0.0
    
    def check_limits(self) -> Tuple[bool, str]:
        """Check if trading is allowed based on limits."""
        now = time.time()
        
        # Check STOP file
        if STOP_FILE.exists():
            return False, "STOP file exists"
        
        # Check if disabled
        if now < self.state.disabled_until:
            return False, f"Disabled until {datetime.fromtimestamp(self.state.disabled_until)}"
        
        # Check daily loss
        equity = self.get_equity()
        if equity > 0:
            daily_loss_pct = (abs(self.state.daily_pnl) if self.state.daily_pnl < 0 else 0) / equity * 100
            if daily_loss_pct > self.config.max_daily_loss_pct:
                return False, f"Daily loss {daily_loss_pct:.1f}% exceeds limit {self.config.max_daily_loss_pct}%"
        
        # Check weekly loss
        if equity > 0:
            weekly_loss_pct = (abs(self.state.weekly_pnl) if self.state.weekly_pnl < 0 else 0) / equity * 100
            if weekly_loss_pct > self.config.max_weekly_loss_pct:
                return False, f"Weekly loss {weekly_loss_pct:.1f}% exceeds limit {self.config.max_weekly_pct}%"
        
        # Check losing streak
        if self.state.losing_streak >= 3:
            return False, f"Losing streak of {self.state.losing_streak}"
        
        # Check open positions
        if len(self.state.positions) >= self.config.max_open_positions:
            return False, f"Max positions ({self.config.max_open_positions}) reached"
        
        return True, ""
    
    def scan_for_signals(self) -> List[Dict]:
        """Scan for trading signals using scanner logic."""
        try:
            # Use scanner's run_scan in quiet mode
            result = scanner.run_scan(quiet=True)
            
            if result.get("paused"):
                log.info(f"Scanner paused: {result.get('reason', 'Unknown')}")
                return []
            
            signals = result.get("signals", [])
            
            # Filter by minimum score (simulated - scanner doesn't have scores)
            # For now, take all signals and rank by volume ratio
            signals.sort(key=lambda x: x.get("volume_ratio", 0), reverse=True)
            
            # Return top signal
            return signals[:1] if signals else []
            
        except Exception as e:
            log.error(f"Scan failed: {e}")
            return []
    
    def enter_trade(self, signal: Dict) -> bool:
        """Enter a trade based on signal."""
        symbol = signal["symbol"]
        price = signal["live_price"]
        
        # Load symbol filters
        if not self.symbol_filters:
            self.symbol_filters = load_symbol_filters(self.client)
        
        if symbol not in self.symbol_filters:
            log.error(f"Symbol {symbol} not in filters")
            return False
        
        filters = self.symbol_filters[symbol]
        
        # Calculate stop (using ATR)
        try:
            df = scanner.get_candles(symbol, "15m", limit=50)
            atr = scanner.atr(df, 14).iloc[-1]
            stop_distance = atr * self.config.stop_atr_mult
            stop = price - stop_distance if signal.get("side", 1) > 0 else price + stop_distance
        except:
            log.error(f"Failed to calculate stop for {symbol}")
            return False
        
        # Calculate position size
        equity = self.get_equity()
        risk_usd = min(self.config.risk_per_trade_usd, equity * 0.01)  # 1% of equity
        
        qty, leverage, reason = calculate_position_size(
            price, stop_distance, risk_usd, filters, equity, self.config.max_leverage
        )
        
        if qty is None:
            log.error(f"Position sizing failed for {symbol}: {reason}")
            return False
        
        # Set leverage and margin type
        try:
            self.client.set_margin_type(symbol, "ISOLATED")
            self.client.set_leverage(symbol, leverage)
        except Exception as e:
            log.error(f"Failed to set leverage for {symbol}: {e}")
            return False
        
        # Place entry order (market)
        side = "BUY" if signal.get("side", 1) > 0 else "SELL"
        try:
            entry_order = self.client.place_order(
                symbol, side, "MARKET", qty, reduce_only=False
            )
            log.info(f"Entry order placed: {entry_order}")
        except Exception as e:
            log.error(f"Entry order failed for {symbol}: {e}")
            return False
        
        # Place stop loss immediately
        try:
            stop_side = "SELL" if side == "BUY" else "BUY"
            stop_order = self.client.place_order(
                symbol, stop_side, "STOP_MARKET", qty,
                stop=stop, reduce_only=True, close_position=True
            )
            log.info(f"Stop order placed: {stop_order}")
        except Exception as e:
            log.error(f"STOP ORDER FAILED for {symbol}: {e}")
            # Emergency close
            try:
                close_side = "SELL" if side == "BUY" else "BUY"
                self.client.place_order(symbol, close_side, "MARKET", qty, reduce_only=True)
                log.error(f"Emergency close executed for {symbol}")
            except:
                log.critical(f"EMERGENCY CLOSE FAILED for {symbol}")
            return False
        
        # Save position to state
        self.state.positions[symbol] = {
            "entry": price,
            "qty": qty,
            "stop": stop,
            "side": side,
            "entry_time": datetime.now(timezone.utc).isoformat(),
            "tp1": price + (price - stop) * self.config.tp1_r if side == "BUY" else price - (stop - price) * self.config.tp1_r,
            "tp2": price + (price - stop) * self.config.tp2_r if side == "BUY" else price - (stop - price) * self.config.tp2_r,
        }
        self.state.save()
        
        log.info(f"Trade entered: {symbol} {side} qty={qty} entry={price} stop={stop}")
        return True
    
    def reconcile_positions(self):
        """Reconcile with exchange - ensure stops are in place."""
        try:
            positions = self.client.get_positions()
            exchange_positions = {p["symbol"]: float(p["positionAmt"]) for p in positions if float(p["positionAmt"]) != 0}
            
            # Check for positions we don't know about
            for symbol, amt in exchange_positions.items():
                if symbol not in self.state.positions:
                    log.warning(f"Found unknown position: {symbol} size={amt}")
                    # Could try to attach stop, but for now just log
            
            # Check for positions we think we have but don't
            for symbol in list(self.state.positions.keys()):
                if symbol not in exchange_positions:
                    log.info(f"Position {symbol} closed externally")
                    del self.state.positions[symbol]
                    self.state.save()
            
        except Exception as e:
            log.error(f"Reconciliation failed: {e}")
    
    def run_cycle(self):
        """Run one trading cycle."""
        now = time.time()
        
        # Check if it's time to scan (hourly at hh:01)
        if not (datetime.fromtimestamp(now).minute == 1 and datetime.fromtimestamp(now).second < 10):
            return
        
        # Check limits
        allowed, reason = self.check_limits()
        if not allowed:
            log.info(f"Trading blocked: {reason}")
            return
        
        # Reconcile positions
        self.reconcile_positions()
        
        # Scan for signals
        signals = self.scan_for_signals()
        
        if not signals:
            log.info("No signals found")
            self.state.last_scan_time = now
            self.state.save()
            return
        
        # Take top signal
        signal = signals[0]
        log.info(f"Top signal: {signal['symbol']}")
        
        # Enter trade
        if self.enter_trade(signal):
            log.info("Trade entered successfully")
        else:
            log.error("Failed to enter trade")
        
        self.state.last_scan_time = now
        self.state.save()
    
    def run(self):
        """Main bot loop."""
        log.info("AutoBot started")
        log.info(f"Mode: {'TESTNET' if self.config.use_testnet else 'LIVE'}")
        log.info(f"Auto-trading: {'ON' if self.state.auto_trading else 'OFF'}")
        
        while True:
            # Check STOP file
            if STOP_FILE.exists():
                log.warning("STOP file found - killing everything")
                self.kill()
                STOP_FILE.unlink()
                continue
            
            # Run cycle if auto-trading is on
            if self.state.auto_trading:
                try:
                    self.run_cycle()
                except Exception as e:
                    log.error(f"Cycle error: {e}")
            
            time.sleep(10)
    
    def kill(self):
        """Emergency kill - close all positions and orders."""
        log.warning("KILL SWITCH activated")
        
        try:
            # Cancel all orders
            for symbol in list(self.state.positions.keys()):
                try:
                    self.client.cancel_all_orders(symbol)
                except:
                    pass
            
            # Close all positions
            for symbol, pos in self.state.positions.items():
                try:
                    side = "SELL" if pos["side"] == "BUY" else "BUY"
                    self.client.place_order(symbol, side, "MARKET", pos["qty"], reduce_only=True)
                    log.info(f"Closed position: {symbol}")
                except:
                    log.error(f"Failed to close {symbol}")
            
            self.state.positions = {}
            self.state.auto_trading = False
            self.state.save()
            
        except Exception as e:
            log.critical(f"Kill failed: {e}")


if __name__ == "__main__":
    bot = AutoBot()
    bot.run()

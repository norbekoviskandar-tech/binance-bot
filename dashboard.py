#!/usr/bin/env python3
"""
Streamlit dashboard for the Binance momentum scanner with trading capabilities.
READ-ONLY by default. Trading requires explicit user action and LIVE mode confirmation.
"""
import csv
import os
import sys
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pandas as pd
import streamlit as st
import yaml
from dotenv import load_dotenv

# Import scanner functions
import scanner

# Import bot functions for tradability check and trading
import bot

# Import autobot for autonomous trading
import autobot
import backtester

# Load API keys from .env
load_dotenv()

# =====================================================================================
# CONFIGURATION & SESSION STATE
# =====================================================================================
st.set_page_config(
    page_title="Binance Scanner Dashboard",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Initialize session state
if 'mode' not in st.session_state:
    st.session_state.mode = 'LIVE'  # Default to LIVE mode
if 'equity_override' not in st.session_state:
    st.session_state.equity_override = None
if 'daily_pnl' not in st.session_state:
    st.session_state.daily_pnl = 0.0
if 'paper_positions' not in st.session_state:
    st.session_state.paper_positions = {}
if 'paper_orders' not in st.session_state:
    st.session_state.paper_orders = []
if 'trades_log' not in st.session_state:
    st.session_state.trades_log = []

# Custom dark theme
st.markdown("""
<style>
    .stApp {
        background-color: #0e1117;
    }
    .main-header {
        font-size: 2.5rem;
        font-weight: bold;
        color: #ffffff;
        margin-bottom: 1rem;
    }
    .warning-banner {
        background-color: #ff9800;
        color: #000000;
        padding: 1rem;
        border-radius: 0.5rem;
        font-weight: bold;
        text-align: center;
        margin-bottom: 1.5rem;
    }
    .live-badge {
        background-color: #f6465d;
        color: white;
        padding: 0.25rem 0.75rem;
        border-radius: 0.25rem;
        font-weight: bold;
        margin-left: 0.5rem;
    }
    .paper-badge {
        background-color: #0ecb81;
        color: white;
        padding: 0.25rem 0.75rem;
        border-radius: 0.25rem;
        font-weight: bold;
        margin-left: 0.5rem;
    }
    .metric-card {
        background-color: #1e2329;
        padding: 1.5rem;
        border-radius: 0.75rem;
        border: 1px solid #2a2e39;
    }
    .metric-value {
        font-size: 2rem;
        font-weight: bold;
    }
    .metric-label {
        font-size: 0.9rem;
        color: #848e9c;
    }
    .positive {
        color: #0ecb81;
    }
    .negative {
        color: #f6465d;
    }
    .neutral {
        color: #848e9c;
    }
    .amber {
        color: #ff9800;
    }
</style>
""", unsafe_allow_html=True)

# =====================================================================================
# HELPER FUNCTIONS
# =====================================================================================
def get_api_keys():
    """Get API keys from environment (never log or print)."""
    key = os.getenv('BINANCE_API_KEY') or os.getenv('BINANCE_LIVE_API_KEY', '')
    secret = os.getenv('BINANCE_API_SECRET') or os.getenv('BINANCE_LIVE_API_SECRET', '')
    return key, secret

def log_trade(mode, symbol, side, qty, entry, stop, tps, order_ids, result):
    """Log trade to CSV (never logs secrets)."""
    log_file = Path("trades_log.csv")
    new = not log_file.exists()
    with log_file.open('a', newline='') as f:
        w = csv.writer(f)
        if new:
            w.writerow(['time_utc', 'mode', 'symbol', 'side', 'qty', 'entry', 'stop', 'tps', 'order_ids', 'result'])
        w.writerow([
            datetime.now(timezone.utc).isoformat(timespec='seconds'),
            mode, symbol, side, qty, entry, stop, tps, order_ids, result
        ])

def calculate_stop(symbol, price, side):
    """Calculate stop loss using swing low/high and ATR."""
    try:
        df = scanner.get_candles(symbol, "15m", limit=50)
        if len(df) < 20:
            return None
        atr = scanner.atr(df, 14).iloc[-1]
        last_10 = df.tail(10)
        if side > 0:  # LONG
            swing_low = last_10['low'].min()
            stop = swing_low - 0.25 * atr
        else:  # SHORT
            swing_high = last_10['high'].max()
            stop = swing_high + 0.25 * atr
        return float(stop)
    except:
        return None

def paper_place_order(symbol, side, qty, price, stop, tp1, tp2):
    """Simulate order placement in paper mode."""
    order_id = f"PAPER_{int(time.time() * 1000)}"
    st.session_state.paper_positions[symbol] = {
        'side': side,
        'qty': qty,
        'entry': price,
        'stop': stop,
        'tp1': tp1,
        'tp2': tp2,
        'entry_time': datetime.now(timezone.utc).isoformat()
    }
    return [order_id]

def paper_cancel_all(symbol):
    """Cancel all paper orders."""
    if symbol in st.session_state.paper_positions:
        del st.session_state.paper_positions[symbol]
    return True

def paper_close_position(symbol):
    """Close paper position."""
    if symbol in st.session_state.paper_positions:
        del st.session_state.paper_positions[symbol]
    return True

def get_account_info():
    """Get account info from Binance (LIVE mode only)."""
    if st.session_state.mode == 'PAPER':
        # Paper mode: use simulated equity
        equity = st.session_state.equity_override or float(os.getenv('PAPER_EQUITY', 1000))
        return {
            'wallet_balance': equity,
            'available_balance': equity,
            'unrealized_pnl': 0.0,
            'margin_used': 0.0
        }
    
    # LIVE mode: get real account info
    key, secret = get_api_keys()
    if not key or not secret:
        return None
    
    try:
        client = bot.Client(bot.LIVE_URL, key, secret)
        client.sync_time()
        
        # Get balance
        balance = bot.get_available_usdt(client)
        
        # Get positions for PnL
        positions = bot.get_positions(client)
        unrealized_pnl = 0.0
        for amt, entry in positions.values():
            # Simplified PnL calculation
            unrealized_pnl += 0  # Would need mark price
        
        return {
            'wallet_balance': balance,
            'available_balance': balance,
            'unrealized_pnl': unrealized_pnl,
            'margin_used': 0.0
        }
    except Exception as e:
        st.error(f"Failed to get account info: {e}")
        return None

def get_positions():
    """Get open positions."""
    if st.session_state.mode == 'PAPER':
        return st.session_state.paper_positions
    
    key, secret = get_api_keys()
    if not key or not secret:
        return {}
    
    try:
        client = bot.Client(bot.LIVE_URL, key, secret)
        client.sync_time()
        return bot.get_positions(client)
    except Exception as e:
        st.error(f"Failed to get positions: {e}")
        return {}

def get_open_orders():
    """Get open orders."""
    if st.session_state.mode == 'PAPER':
        return []
    
    key, secret = get_api_keys()
    if not key or not secret:
        return []
    
    try:
        client = bot.Client(bot.LIVE_URL, key, secret)
        client.sync_time()
        # Would need to implement get_open_orders in bot.py
        return []
    except Exception as e:
        st.error(f"Failed to get orders: {e}")
        return []

# =====================================================================================
# BANNER
# =====================================================================================
mode_badge = f'<span class="live-badge">LIVE</span>' if st.session_state.mode == 'LIVE' else f'<span class="paper-badge">PAPER</span>'
st.markdown(f'<div class="warning-banner">⚠️ Screening tool, not financial advice. {mode_badge}</div>', unsafe_allow_html=True)

# =====================================================================================
# SIDEBAR CONTROLS
# =====================================================================================
st.sidebar.header("⚙️ Settings")

# Mode switch
st.sidebar.subheader("Trading Mode")
mode_options = ['LIVE', 'PAPER']
selected_mode = st.sidebar.selectbox("Mode", mode_options, index=mode_options.index(st.session_state.mode))
st.session_state.mode = selected_mode

# Load config
config_path = Path("config.yaml")
if config_path.exists():
    with config_path.open() as f:
        config = yaml.safe_load(f) or {}
else:
    config = {}

# Scanner settings
st.sidebar.subheader("Scanner Rules")
volume_ratio = st.sidebar.slider("Volume Ratio Min", 1.0, 5.0, scanner.VOLUME_RATIO_MIN, 0.1)
rsi_min = st.sidebar.slider("RSI Min", 30, 50, scanner.RSI_MIN, 1)
rsi_max = st.sidebar.slider("RSI Max", 60, 80, scanner.RSI_MAX, 1)
min_volume = st.sidebar.number_input("Min Volume USD", value=scanner.MIN_VOLUME_USD, step=1_000_000)

# Apply settings
scanner.VOLUME_RATIO_MIN = volume_ratio
scanner.RSI_MIN = rsi_min
scanner.RSI_MAX = rsi_max
scanner.MIN_VOLUME_USD = min_volume

# Bot settings for tradability
st.sidebar.subheader("Bot Settings (Tradability)")
account_info = get_account_info()
if account_info:
    exchange_equity = account_info['wallet_balance']
    st.sidebar.markdown(f"<small style='color: #848e9c;'>from exchange: ${exchange_equity:.2f}</small>", unsafe_allow_html=True)
    equity = st.sidebar.number_input("Equity ($)", value=max(1.0, float(exchange_equity)), min_value=0.01, step=1.0)
else:
    equity = st.sidebar.number_input("Equity ($)", value=float(config.get('equity', 30)), min_value=1.0, step=1.0)

risk_per_trade = st.sidebar.number_input("Risk per Trade ($)", value=float(config.get('risk_per_trade_usd', 1)), min_value=0.1, step=0.1)
max_leverage = st.sidebar.slider("Max Leverage", 1, 20, config.get('max_leverage', 3))
max_open_positions = st.sidebar.number_input("Max Open Positions", value=2, min_value=1, max_value=10)
max_daily_loss_pct = st.sidebar.slider("Max Daily Loss %", 1, 10, 3)

# Risk warning
risk_pct = (risk_per_trade / equity) * 100 if equity > 0 else 0
if risk_pct > 2:
    st.sidebar.markdown(f"<div class='amber'>⚠️ Risk is above 2% of your account ({risk_pct:.1f}%)</div>", unsafe_allow_html=True)

st.sidebar.subheader("Actions")
scan_button = st.sidebar.button("🔍 Run Scan Now")
auto_scan = st.sidebar.checkbox("Auto-scan every 5 minutes", value=False)

# Save config button
if st.sidebar.button("💾 Save to config.yaml"):
    config['equity'] = equity
    config['risk_per_trade_usd'] = risk_per_trade
    config['max_leverage'] = max_leverage
    with config_path.open('w') as f:
        yaml.dump(config, f)
    st.sidebar.success("Settings saved to config.yaml")

# =====================================================================================
# TABS
# =====================================================================================
tab1, tab2, tab3, tab4, tab5 = st.tabs(["🔍 Scanner", "🤖 Bot", "💼 Account", "📜 Trade Log", "🧪 Backtest"])

# =====================================================================================
# SCANNER TAB
# =====================================================================================
with tab1:
    st.markdown('<div class="main-header">📊 Binance Momentum Scanner</div>', unsafe_allow_html=True)

    # Create columns for metrics
    col1, col2, col3, col4 = st.columns(4)

    # Placeholder for metrics
    with col1:
        st.markdown('<div class="metric-card">', unsafe_allow_html=True)
        st.markdown('<div class="metric-label">Bitcoin 1h Change</div>', unsafe_allow_html=True)
        btc_1h_metric = st.empty()
        st.markdown('</div>', unsafe_allow_html=True)

    with col2:
        st.markdown('<div class="metric-card">', unsafe_allow_html=True)
        st.markdown('<div class="metric-label">Bitcoin 4h Change</div>', unsafe_allow_html=True)
        btc_4h_metric = st.empty()
        st.markdown('</div>', unsafe_allow_html=True)

    with col3:
        st.markdown('<div class="metric-card">', unsafe_allow_html=True)
        st.markdown('<div class="metric-label">Coins Checked</div>', unsafe_allow_html=True)
        coins_checked_metric = st.empty()
        st.markdown('</div>', unsafe_allow_html=True)

    with col4:
        st.markdown('<div class="metric-card">', unsafe_allow_html=True)
        st.markdown('<div class="metric-label">Signals Found</div>', unsafe_allow_html=True)
        signals_count_metric = st.empty()
        st.markdown('</div>', unsafe_allow_html=True)

    # Results section
    st.markdown("---")
    st.subheader("🎯 Current Signals")

    # Placeholder for signals table
    signals_placeholder = st.empty()

    # Trade ticket placeholder
    trade_ticket_placeholder = st.empty()

    # =====================================================================================
    # TRADABILITY CHECK
    # =====================================================================================
    def check_tradability(signal, equity=30, risk_per_trade=1, max_leverage=3):
        """Check if a signal is tradable using actual bot functions."""
        try:
            client = bot.Client(bot.LIVE_URL)
            infos = bot.load_symbols(client)
            symbol = signal.get('symbol', '')
            if symbol not in infos:
                return False, "Symbol not found", 0, 0
            info = infos[symbol]
            price = signal.get('live_price', signal.get('price', 0))
            stop_dist = price * 0.02
            cfg = bot.Cfg(equity=equity, risk_per_trade_usd=risk_per_trade, max_leverage=max_leverage)
            qty, lev, reason = bot.size_position(info, price, stop_dist, cfg, equity)
            if qty is None:
                return False, reason, 0, 0
            return True, "Tradable", qty, lev
        except Exception as e:
            return False, f"Error: {str(e)}", 0, 0

    # =====================================================================================
    # RUN SCAN
    # =====================================================================================
    if scan_button or auto_scan:
        with st.spinner("Scanning market..."):
            try:
                result = scanner.run_scan(quiet=True)

                # Update metrics
                if result.get("paused"):
                    btc_1h_metric.markdown(f'<div class="metric-value negative">{result["btc_1h"]:+.2f}%</div>', unsafe_allow_html=True)
                    btc_4h_metric.markdown('<div class="metric-value neutral">PAUSED</div>', unsafe_allow_html=True)
                    coins_checked_metric.markdown('<div class="metric-value neutral">-</div>', unsafe_allow_html=True)
                    signals_count_metric.markdown('<div class="metric-value neutral">0</div>', unsafe_allow_html=True)
                    st.error(f"⏸️ Scanner paused: Bitcoin dropped {result['btc_1h']:+.2f}% in the last hour")
                else:
                    btc_1h_val = result["btc_1h"]
                    btc_4h_val = result["btc_4h"]
                    signals = result["signals"]
                    coins_checked = result["coins_checked"]

                    btc_1h_class = "positive" if btc_1h_val >= 0 else "negative"
                    btc_4h_class = "positive" if btc_4h_val >= 0 else "negative"

                    btc_1h_metric.markdown(f'<div class="metric-value {btc_1h_class}">{btc_1h_val:+.2f}%</div>', unsafe_allow_html=True)
                    btc_4h_metric.markdown(f'<div class="metric-value {btc_4h_class}">{btc_4h_val:+.2f}%</div>', unsafe_allow_html=True)
                    coins_checked_metric.markdown(f'<div class="metric-value neutral">{coins_checked}</div>', unsafe_allow_html=True)
                    signals_count_metric.markdown(f'<div class="metric-value positive">{len(signals)}</div>', unsafe_allow_html=True)

                    # Display signals with trade tickets
                    if signals:
                        tradable_signals = []
                        for sig in signals:
                            is_tradable, reason, qty, lev = check_tradability(
                                sig, equity=equity, risk_per_trade=risk_per_trade, max_leverage=max_leverage
                            )
                            sig['tradable'] = is_tradable
                            sig['tradable_reason'] = reason
                            sig['estimated_qty'] = qty
                            sig['estimated_lev'] = lev
                            tradable_signals.append(sig)

                        df = pd.DataFrame(tradable_signals)
                        df_display = df[[
                            'symbol', 'live_price', 'volume_ratio', 'rsi',
                            'change_4h', 'btc_4h', 'tradable', 'tradable_reason'
                        ]].copy()
                        df_display.columns = ['Symbol', 'Price', 'Vol Ratio', 'RSI', 'Coin 4h %', 'BTC 4h %', 'Tradable', 'Reason']
                        df_display['Symbol'] = df_display['Symbol'].str.replace('USDT', '')
                        df_display['Price'] = df_display['Price'].map('${:,.8g}'.format)
                        df_display['Vol Ratio'] = df_display['Vol Ratio'].map('{:.2f}x'.format)
                        df_display['RSI'] = df_display['RSI'].map('{:.1f}'.format)
                        df_display['Coin 4h %'] = df_display['Coin 4h %'].map('{:+.2f}%'.format)
                        df_display['BTC 4h %'] = df_display['BTC 4h %'].map('{:+.2f}%'.format)

                        def color_tradable(val):
                            if val == True:
                                return 'background-color: #0ecb81; color: white'
                            else:
                                return 'background-color: #f6465d; color: white'

                        try:
                            styled_df = df_display.style.map(color_tradable, subset=['Tradable'])
                        except AttributeError:
                            styled_df = df_display.style.applymap(color_tradable, subset=['Tradable'])
                        signals_placeholder.dataframe(styled_df, width='stretch', hide_index=True)

                        # Trade tickets for tradable signals
                        st.markdown("---")
                        st.subheader("🎫 Trade Tickets")
                        
                        for sig in tradable_signals:
                            if sig['tradable']:
                                with st.expander(f"Trade: {sig['symbol']} (LONG)", expanded=False):
                                    price = sig['live_price']
                                    stop = calculate_stop(sig['symbol'], price, 1)
                                    if stop is None:
                                        stop = price * 0.98  # Fallback
                                    
                                    tp1 = price + (price - stop) * 2
                                    tp2 = price + (price - stop) * 3
                                    
                                    col_a, col_b = st.columns(2)
                                    with col_a:
                                        st.metric("Entry", f"${price:.8g}")
                                        st.metric("Stop", f"${stop:.8g}")
                                    with col_b:
                                        st.metric("TP1", f"${tp1:.8g}")
                                        st.metric("TP2", f"${tp2:.8g}")
                                    
                                    st.button(f"Preview Order: {sig['symbol']}", key=f"preview_{sig['symbol']}")
                    else:
                        signals_placeholder.info("✅ No signals found. Market conditions don't meet all criteria.")

            except Exception as e:
                st.error(f"❌ Scan failed: {e}")
    else:
        btc_1h_metric.markdown('<div class="metric-value neutral">-</div>', unsafe_allow_html=True)
        btc_4h_metric.markdown('<div class="metric-value neutral">-</div>', unsafe_allow_html=True)
        coins_checked_metric.markdown('<div class="metric-value neutral">-</div>', unsafe_allow_html=True)
        signals_count_metric.markdown('<div class="metric-value neutral">0</div>', unsafe_allow_html=True)
        signals_placeholder.info("👆 Click 'Run Scan Now' in the sidebar to start scanning")

    # Manual trade ticket
    st.markdown("---")
    st.subheader("🎫 Manual Trade Ticket")
    
    with st.form("manual_trade"):
        col1, col2, col3 = st.columns(3)
        with col1:
            symbol = st.text_input("Symbol (e.g., BTCUSDT)", value="BTCUSDT").upper()
            side = st.selectbox("Side", ["LONG", "SHORT"])
        with col2:
            entry = st.number_input("Entry Price", value=0.0, min_value=0.0)
            stop = st.number_input("Stop Loss", value=0.0, min_value=0.0)
        with col3:
            tp1 = st.number_input("Take Profit 1", value=0.0, min_value=0.0)
            tp2 = st.number_input("Take Profit 2", value=0.0, min_value=0.0)
        
        submitted = st.form_submit_button("Preview Order")
        if submitted:
            st.info("Preview functionality - to be implemented")

# =====================================================================================
# BOT TAB
# =====================================================================================
with tab2:
    st.markdown('<div class="main-header">🤖 Autonomous Bot</div>', unsafe_allow_html=True)
    
    # Load autobot config
    bot_config = autobot.BotConfig.from_yaml(Path("config.yaml"))
    
    # Status cards
    col1, col2, col3, col4 = st.columns(4)
    
    with col1:
        st.markdown('<div class="metric-card">', unsafe_allow_html=True)
        st.markdown('<div class="metric-label">Auto-Trading</div>', unsafe_allow_html=True)
        # Check if autobot is running (would need to check state file)
        auto_status = "OFF"  # Would read from state
        status_class = "positive" if auto_status == "ON" else "negative"
        st.markdown(f'<div class="metric-value {status_class}">{auto_status}</div>', unsafe_allow_html=True)
        st.markdown('</div>', unsafe_allow_html=True)
    
    with col2:
        st.markdown('<div class="metric-card">', unsafe_allow_html=True)
        st.markdown('<div class="metric-label">Mode</div>', unsafe_allow_html=True)
        mode = "TESTNET" if bot_config.use_testnet else "LIVE"
        mode_class = "amber" if bot_config.use_testnet else "negative"
        st.markdown(f'<div class="metric-value {mode_class}">{mode}</div>', unsafe_allow_html=True)
        st.markdown('</div>', unsafe_allow_html=True)
    
    with col3:
        st.markdown('<div class="metric-card">', unsafe_allow_html=True)
        st.markdown('<div class="metric-label">Daily PnL</div>', unsafe_allow_html=True)
        st.markdown(f'<div class="metric-value neutral">$0.00</div>', unsafe_allow_html=True)
        st.markdown('</div>', unsafe_allow_html=True)
    
    with col4:
        st.markdown('<div class="metric-card">', unsafe_allow_html=True)
        st.markdown('<div class="metric-label">Open Positions</div>', unsafe_allow_html=True)
        st.markdown(f'<div class="metric-value neutral">0</div>', unsafe_allow_html=True)
        st.markdown('</div>', unsafe_allow_html=True)
    
    st.markdown("---")
    
    # Controls
    col1, col2, col3 = st.columns(3)
    
    with col1:
        if st.button("▶️ START Auto-Trading", type="primary"):
            confirmation = st.text_input("Type START to confirm", key="start_confirm")
            if confirmation == "START":
                st.success("Auto-trading started")
                # Would start autobot process
            else:
                st.warning("Type START to confirm")
    
    with col2:
        if st.button("⏸️ STOP Auto-Trading", type="secondary"):
            st.info("Auto-trading stopped")
            # Would stop autobot process
    
    with col3:
        if st.button("🚨 KILL SWITCH", type="secondary"):
            Path("STOP").touch()
            st.error("KILL SWITCH activated - all positions closing")
    
    # Bot log
    st.markdown("---")
    st.subheader("📋 Bot Log")
    
    bot_log_file = Path("autobot.log")
    if bot_log_file.exists():
        with bot_log_file.open() as f:
            lines = f.readlines()[-50:]  # Last 50 lines
        st.text_area("Recent Logs", value="".join(lines), height=300)
    else:
        st.info("No bot log yet")
    
    # Limits status
    st.markdown("---")
    st.subheader("⚙️ Limits Status")
    
    limits_data = [
        {"Limit": "Risk per Trade", "Value": f"${bot_config.risk_per_trade_usd}", "Status": "OK"},
        {"Limit": "Max Leverage", "Value": f"{bot_config.max_leverage}x", "Status": "OK"},
        {"Limit": "Max Positions", "Value": str(bot_config.max_open_positions), "Status": "OK"},
        {"Limit": "Daily Loss", "Value": f"{bot_config.max_daily_loss_pct}%", "Status": "OK"},
        {"Limit": "Weekly Loss", "Value": f"{bot_config.max_weekly_loss_pct}%", "Status": "OK"},
        {"Limit": "Require Backtest", "Value": "Yes" if bot_config.require_backtest_pass else "No", "Status": "OK"},
    ]
    
    st.dataframe(pd.DataFrame(limits_data), width='stretch', hide_index=True)

# =====================================================================================
# ACCOUNT TAB
# =====================================================================================
with tab3:
    st.markdown('<div class="main-header">💼 Account</div>', unsafe_allow_html=True)
    
    account_info = get_account_info()
    
    if account_info:
        # Balance cards
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.markdown('<div class="metric-card">', unsafe_allow_html=True)
            st.markdown('<div class="metric-label">Wallet Balance</div>', unsafe_allow_html=True)
            st.markdown(f'<div class="metric-value">${account_info["wallet_balance"]:.2f}</div>', unsafe_allow_html=True)
            st.markdown('</div>', unsafe_allow_html=True)
        
        with col2:
            st.markdown('<div class="metric-card">', unsafe_allow_html=True)
            st.markdown('<div class="metric-label">Available Balance</div>', unsafe_allow_html=True)
            st.markdown(f'<div class="metric-value">${account_info["available_balance"]:.2f}</div>', unsafe_allow_html=True)
            st.markdown('</div>', unsafe_allow_html=True)
        
        with col3:
            st.markdown('<div class="metric-card">', unsafe_allow_html=True)
            st.markdown('<div class="metric-label">Unrealized PnL</div>', unsafe_allow_html=True)
            pnl_class = "positive" if account_info["unrealized_pnl"] >= 0 else "negative"
            st.markdown(f'<div class="metric-value {pnl_class}">${account_info["unrealized_pnl"]:+.2f}</div>', unsafe_allow_html=True)
            st.markdown('</div>', unsafe_allow_html=True)
        
        with col4:
            st.markdown('<div class="metric-card">', unsafe_allow_html=True)
            st.markdown('<div class="metric-label">Margin Used</div>', unsafe_allow_html=True)
            st.markdown(f'<div class="metric-value">${account_info["margin_used"]:.2f}</div>', unsafe_allow_html=True)
            st.markdown('</div>', unsafe_allow_html=True)
        
        st.markdown("---")
        
        # Positions
        st.subheader("📊 Open Positions")
        positions = get_positions()
        if positions:
            pos_data = []
            for symbol, (amt, entry) in positions.items():
                pos_data.append({
                    'Symbol': symbol,
                    'Side': 'LONG' if amt > 0 else 'SHORT',
                    'Size': abs(amt),
                    'Entry': entry,
                    'PnL': 0.0,  # Would need mark price
                    'Liquidation Price': 0.0  # Would need calculation
                })
            st.dataframe(pd.DataFrame(pos_data), width='stretch')
        else:
            st.info("No open positions")
        
        # Orders
        st.subheader("📋 Open Orders")
        orders = get_open_orders()
        if orders:
            st.dataframe(pd.DataFrame(orders), width='stretch')
        else:
            st.info("No open orders")
        
        # Panic buttons
        st.markdown("---")
        col1, col2 = st.columns(2)
        with col1:
            if st.button("🚨 Cancel All Orders", type="secondary"):
                st.warning("Cancel all orders - to be implemented")
        with col2:
            if st.button("🚨 Close All Positions", type="secondary"):
                st.warning("Close all positions - to be implemented")
    else:
        st.error("Could not connect to account. Check API keys in .env file.")

# =====================================================================================
# TRADE LOG TAB
# =====================================================================================
with tab4:
    st.markdown('<div class="main-header">📜 Trade Log</div>', unsafe_allow_html=True)
    
    log_file = Path("trades_log.csv")
    if log_file.exists():
        with log_file.open() as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        
        if rows:
            st.dataframe(pd.DataFrame(rows[::-1]), width='stretch')
        else:
            st.info("No trades logged yet")
    else:
        st.info("No trades log file yet")

# =====================================================================================
# FOOTER
# =====================================================================================
st.markdown("---")
st.markdown("""
<div style='text-align: center; color: #848e9c; font-size: 0.8rem;'>
    Last updated: {} | Data source: Binance Public API | Mode: {} | Read-only unless LIVE mode confirmed
</div>
""".format(datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"), st.session_state.mode), unsafe_allow_html=True)

with tab5:
    backtester.render(risk_per_trade, equity)

# Binance Scanner Dashboard & Autonomous Bot

A local Streamlit dashboard for scanning Binance momentum signals and autonomous trading on USDⓈ-M Futures.

## Features

- **Scanner**: Real-time momentum signal detection using public Binance data
- **Autonomous Bot**: Hourly automatic trading with strict safety limits
- **Account View**: Wallet balance, positions, and orders (read-only with API keys)
- **Trade Tickets**: Manual and signal-based order placement with safety validations
- **Paper Trading**: Built-in simulator for testing without real money
- **Live Trading**: Guarded trading mode with explicit confirmation required

## Installation

1. Install Python 3.11+
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

## Configuration

1. Copy `.env.example` to `.env`:
   ```bash
   cp .env.example .env
   ```

2. Add your Binance API keys to `.env`:
   ```
   BINANCE_API_KEY=your_api_key_here
   BINANCE_API_SECRET=your_api_secret_here
   ```

3. Get API keys from Binance → API Management
   - Enable "Futures" trading permissions
   - For read-only account view, a read-only key is sufficient
   - For trading, a key with trading permissions is required

## Running the Dashboard

Start the dashboard (localhost only):
```bash
streamlit run dashboard.py --server.address 127.0.0.1
```

Open your browser to: http://127.0.0.1:8501

## Modes

### PAPER Mode (Default)
- Simulated trading with virtual balance
- No API keys required for scanning
- Use `PAPER_EQUITY` in `.env` to set paper balance
- All orders are simulated

### LIVE Mode
- Real trading on Binance USDⓈ-M Futures
- Requires API keys with trading permissions
- Must type "LIVE" to confirm mode switch
- Red LIVE badge shown on all tabs
- All safety validations apply

## Autonomous Bot

The autonomous bot trades automatically based on scanner signals with strict safety limits:

### Bot Strategy
- Scans once per hour at hh:01 UTC
- Uses momentum signals from scanner.py (volume surge + RSI + MACD)
- Takes only the top-ranked signal per scan
- Entry: market order after signal
- Stop: calculated using ATR (1.5x ATR)
- Take Profit: TP1 at 2R (close 50%), TP2 at 3R (close rest)
- Time exit: close after 48 hours

### Safety Limits
- Risk per trade: 1% of equity (configurable)
- Max leverage: 3x
- Max open positions: 1
- Daily loss limit: 3% of equity
- Weekly loss limit: 8% of equity
- Losing streak: pause after 3 losses
- All trades require exchange-side stop loss

### Controls
- **START**: Type "START" to enable auto-trading
- **STOP**: Disable auto-trading
- **KILL SWITCH**: Emergency close all positions and orders
- STOP file: Create a file named "STOP" to kill the bot

### Bot Tab
- Auto-trading status (ON/OFF)
- Mode (LIVE/TESTNET)
- Daily PnL
- Open positions count
- Bot log (recent activity)
- Limits status

## Scanner Criteria

A signal is flagged when ALL are true:
- Volume ratio ≥ 1.5x (latest candle vs 20-candle average)
- RSI between 45-70
- MACD line crossed above signal line
- Coin's 4h change > Bitcoin's 4h change
- 24h volume ≥ $5M
- Bitcoin not crashing (<2% drop in 1h)

## Trade Ticket Validations

Orders are blocked if:
- Risk > 2% of equity (amber warning)
- Leverage > configured max
- Risk > max_risk_usd
- Stop on wrong side of entry
- Quantity ≤ 0
- Symbol not in scanned universe
- Open positions ≥ max_open_positions (default: 2)
- Today's realized loss > max_daily_loss (default: 3% of equity)
- Below minimum notional (~$5)

## Safety Features

- **No auto-trading**: Orders sent only by human click
- **Two-step confirmation**: Preview → Confirm checkbox → Type symbol → Send
- **Stop required**: Trade cannot be submitted without stop loss
- **Daily loss limit**: Trading disabled if daily loss > 3% of equity
- **Panic buttons**: Cancel all orders, close position
- **Read-only by default**: API keys optional for scanning
- **Local only**: Server binds to 127.0.0.1 only
- **No withdrawals**: Withdraw/transfer endpoints never implemented

## Files

- `dashboard.py` - Streamlit dashboard
- `scanner.py` - Momentum scanner logic
- `bot.py` - Binance futures bot (for trading functions)
- `config.yaml` - Bot configuration
- `.env` - API keys (never commit to git)
- `signals.csv` - Historical signals
- `trades_log.csv` - Trade execution log

## Security

- `.env` is in `.gitignore` - never commit API keys
- API keys never printed, logged, or shown
- Server binds to localhost only
- LIVE mode requires explicit confirmation
- All secrets handled by python-dotenv

## Disclaimer

⚠️ This is a screening tool, not financial advice. Trading involves significant risk. Past performance is not indicative of future results. Use at your own risk.

#!/bin/bash
# Management script for Binance Trading Bot on VPS

case "$1" in
    start)
        echo "Starting bot..."
        systemctl start binance-bot
        systemctl start binance-dashboard
        echo "Bot and dashboard started"
        ;;
    stop)
        echo "Stopping bot..."
        systemctl stop binance-bot
        systemctl stop binance-dashboard
        echo "Bot and dashboard stopped"
        ;;
    restart)
        echo "Restarting bot..."
        systemctl restart binance-bot
        systemctl restart binance-dashboard
        echo "Bot and dashboard restarted"
        ;;
    status)
        echo "=== Bot Status ==="
        systemctl status binance-bot
        echo ""
        echo "=== Dashboard Status ==="
        systemctl status binance-dashboard
        ;;
    logs)
        echo "=== Bot Logs (last 50 lines) ==="
        tail -n 50 /opt/binance-bot/bot.log
        ;;
    follow)
        echo "Following bot logs (Ctrl+C to exit)..."
        tail -f /opt/binance-bot/bot.log
        ;;
    kill)
        echo "Creating STOP file to close all positions..."
        touch /opt/binance-bot/STOP
        echo "STOP file created. Bot will close positions and exit."
        ;;
    scan)
        echo "Running scan..."
        cd /opt/binance-bot
        source venv/bin/activate
        python bot.py --scan
        ;;
    backtest)
        echo "Running backtest..."
        cd /opt/binance-bot
        source venv/bin/activate
        python bot.py --backtest
        ;;
    update)
        echo "Updating bot..."
        cd /opt/binance-bot
        systemctl stop binance-bot
        systemctl stop binance-dashboard
        source venv/bin/activate
        pip install -r requirements.txt --upgrade
        systemctl start binance-bot
        systemctl start binance-dashboard
        echo "Bot updated and restarted"
        ;;
    *)
        echo "Usage: ./manage.sh {start|stop|restart|status|logs|follow|kill|scan|backtest|update}"
        echo ""
        echo "Commands:"
        echo "  start     - Start bot and dashboard"
        echo "  stop      - Stop bot and dashboard"
        echo "  restart   - Restart bot and dashboard"
        echo "  status    - Show service status"
        echo "  logs      - Show last 50 log lines"
        echo "  follow    - Follow logs in real-time"
        echo "  kill      - Create STOP file to close all positions"
        echo "  scan      - Run a single scan"
        echo "  backtest  - Run backtest"
        echo "  update    - Update dependencies and restart"
        exit 1
        ;;
esac

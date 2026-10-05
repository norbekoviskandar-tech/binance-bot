#!/bin/bash
# Deployment script for Binance Trading Bot on Ubuntu VPS

set -e

echo "=== Binance Trading Bot Deployment ==="
echo ""

# Check if running as root
if [ "$EUID" -ne 0 ]; then 
    echo "Please run as root (use sudo)"
    exit 1
fi

# Update system
echo "Updating system packages..."
apt update && apt upgrade -y

# Install dependencies
echo "Installing Python and dependencies..."
apt install -y python3 python3-pip python3-venv git htop screen

# Create bot directory
echo "Setting up bot directory..."
mkdir -p /opt/binance-bot
cd /opt/binance-bot

# Create virtual environment
echo "Creating Python virtual environment..."
python3 -m venv venv
source venv/bin/activate

# Install Python packages
echo "Installing Python packages..."
pip install --upgrade pip
pip install -r requirements.txt

# Create systemd service
echo "Creating systemd service..."
cat > /etc/systemd/system/binance-bot.service << 'EOF'
[Unit]
Description=Binance Trading Bot
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/binance-bot
Environment="PATH=/opt/binance-bot/venv/bin"
ExecStart=/opt/binance-bot/venv/bin/python bot.py --live
Restart=always
RestartSec=10
StandardOutput=append:/opt/binance-bot/bot.log
StandardError=append:/opt/binance-bot/bot.log

[Install]
WantedBy=multi-user.target
EOF

# Create dashboard service
echo "Creating dashboard service..."
cat > /etc/systemd/system/binance-dashboard.service << 'EOF'
[Unit]
Description=Binance Trading Bot Dashboard
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/binance-bot
Environment="PATH=/opt/binance-bot/venv/bin"
ExecStart=/opt/binance-bot/venv/bin/streamlit run dashboard.py --server.port=8501 --server.address=0.0.0.0
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

# Create log rotation
echo "Setting up log rotation..."
cat > /etc/logrotate.d/binance-bot << 'EOF'
/opt/binance-bot/*.log {
    daily
    rotate 7
    compress
    missingok
    notifempty
    create 644 root root
}
EOF

# Set permissions
echo "Setting permissions..."
chmod +x /opt/binance-bot/deploy.sh

echo ""
echo "=== Deployment Complete ==="
echo ""
echo "Next steps:"
echo "1. Copy your bot files to /opt/binance-bot/"
echo "2. Copy your .env file to /opt/binance-bot/.env"
echo "3. Start the bot: systemctl start binance-bot"
echo "4. Start the dashboard: systemctl start binance-dashboard"
echo "5. Enable auto-start on boot:"
echo "   systemctl enable binance-bot"
echo "   systemctl enable binance-dashboard"
echo ""
echo "Check status:"
echo "  systemctl status binance-bot"
echo "  systemctl status binance-dashboard"
echo ""
echo "View logs:"
echo "  tail -f /opt/binance-bot/bot.log"
echo ""

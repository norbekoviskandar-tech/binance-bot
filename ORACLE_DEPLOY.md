# Oracle Cloud Free Tier Deployment Guide

## Why Oracle Cloud Free Tier?

- **$0 forever** - no credit card required for verification
- **Always free** - no time limit
- **Good specs**: 4 ARM CPUs, 24GB RAM, 200GB storage
- **Perfect for trading bot**

## Step 1: Create Oracle Cloud Account

1. Go to: https://www.oracle.com/cloud/free/
2. Click "Try for Free"
3. Sign up with email
4. Verify email address
5. You'll get:
   - 2 AMD Compute VMs (1/8 OCPU, 1GB RAM)
   - 4 ARM Ampere A1 Compute VMs (4 OCPU, 24GB RAM) ⭐ Use this one
   - 200GB Block Volume storage
   - 10TB/month outbound data transfer

## Step 2: Create Free Tier VM

1. Log into Oracle Cloud Console
2. Go to "Compute" → "Instances"
3. Click "Create Instance"
4. **Name**: binance-bot
5. **Compartment**: Your compartment
6. **Shape**: Choose "Always Free" tier
   - Select: **VM.Standard.E2.1.Micro** (AMD) OR **VM.Standard.A1.Flex** (ARM)
   - For ARM: Set OCPU to 4, Memory to 24GB (max free tier)
7. **Image**: Ubuntu 22.04 Minimal
8. **SSH Keys**: Add your SSH public key OR use password
9. **Networking**: Keep defaults (public IP)
10. Click "Create"

Wait 5-10 minutes for instance to be ready.

## Step 3: Connect to Your VM

**If using SSH key:**
```bash
ssh -i /path/to/your-key.pem ubuntu@YOUR_PUBLIC_IP
```

**If using password:**
```bash
ssh ubuntu@YOUR_PUBLIC_IP
# Enter password when prompted
```

## Step 4: Set Up the Bot

Once connected:

```bash
# Update system
sudo apt update && sudo apt upgrade -y

# Install dependencies
sudo apt install -y python3 python3-pip python3-venv git

# Create bot directory
sudo mkdir -p /opt/binance-bot
cd /opt/binance-bot

# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install Python packages
pip install --upgrade pip
# You'll need to upload requirements.txt and install from it
```

## Step 5: Upload Files

**From your Windows machine:**

Option A - Using SCP (if you have PuTTY):
```bash
pscp bot.py config.yaml requirements.txt dashboard.py scanner.py ubuntu@YOUR_PUBLIC_IP:/home/ubuntu/
```

Option B - Using WinSCP (easier):
1. Download WinSCP: https://winscp.net/
2. Connect to your Oracle VM
3. Drag and drop files to /home/ubuntu/

## Step 6: Move Files and Complete Setup

On the Oracle VM:

```bash
# Move files to correct location
sudo mv /home/ubuntu/*.py /opt/binance-bot/
sudo mv /home/ubuntu/*.yaml /opt/binance-bot/
sudo mv /home/ubuntu/*.txt /opt/binance-bot/

# Set permissions
cd /opt/binance-bot
sudo chown -R ubuntu:ubuntu /opt/binance-bot

# Install dependencies
source venv/bin/activate
pip install -r requirements.txt

# Create .env file
nano .env
# Add your API keys here
```

## Step 7: Run the Bot

```bash
# Test scan first
source venv/bin/activate
python bot.py --scan

# If scan works, run live (if you have API keys)
python bot.py --live
```

## Step 8: Run in Background

```bash
# Install screen for background process
sudo apt install screen

# Start screen session
screen -S bot

# Start bot
source venv/bin/activate
python bot.py --live

# Detach from screen (Ctrl+A, then D)
# Bot will keep running in background

# To reattach:
screen -r bot
```

## Step 9: Auto-Start on Boot

Create a systemd service:

```bash
sudo nano /etc/systemd/system/binance-bot.service
```

Add this content:
```ini
[Unit]
Description=Binance Trading Bot
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/opt/binance-bot
Environment="PATH=/opt/binance-bot/venv/bin"
ExecStart=/opt/binance-bot/venv/bin/python bot.py --live
Restart=always
RestartSec=10
StandardOutput=append:/opt/binance-bot/bot.log
StandardError=append:/opt/binance-bot/bot.log

[Install]
WantedBy=multi-user.target
```

Enable and start:
```bash
sudo systemctl enable binance-bot
sudo systemctl start binance-bot
sudo systemctl status binance-bot
```

## Important Notes

### Oracle Cloud Free Tier Limitations:
- **No GPU** (not needed for this bot)
- **ARM architecture** (works fine with Python)
- **Network bandwidth**: 10TB/month outbound (plenty for API calls)
- **Storage**: 200GB (plenty)

### Keep Your Free Tier Active:
- Login to Oracle Cloud console at least once every 90 days
- Use the resources occasionally (bot running counts as usage)

### Security:
- Change default SSH port (optional)
- Use SSH keys instead of password (recommended)
- Set up firewall (iptables or UFW)

## Monitoring

```bash
# Check bot status
sudo systemctl status binance-bot

# View logs
tail -f /opt/binance-bot/bot.log

# Restart bot
sudo systemctl restart binance-bot
```

## Dashboard

To run the dashboard:

```bash
# Create dashboard service
sudo nano /etc/systemd/system/binance-dashboard.service
```

Add:
```ini
[Unit]
Description=Binance Trading Bot Dashboard
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/opt/binance-bot
Environment="PATH=/opt/binance-bot/venv/bin"
ExecStart=/opt/binance-bot/venv/bin/streamlit run dashboard.py --server.port=8501 --server.address=0.0.0.0
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Start dashboard:
```bash
sudo systemctl enable binance-dashboard
sudo systemctl start binance-dashboard
```

Access at: `http://YOUR_PUBLIC_IP:8501`

## Troubleshooting

### Instance won't start
- Check if you're within free tier limits
- Verify your account is active
- Check Oracle Cloud console for errors

### Can't connect via SSH
- Check security list (firewall) in Oracle Cloud Console
- Ensure port 22 is open
- Verify your public IP is correct

### Bot crashes
- Check logs: `tail -f /opt/binance-bot/bot.log`
- Verify .env file has correct API keys
- Check Python dependencies are installed

## Total Cost: $0 Forever! 🎉

This setup costs absolutely nothing and will run your bot 24/7 on Oracle's infrastructure.

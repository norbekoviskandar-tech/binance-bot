# Binance Trading Bot - VPS Deployment Guide

## Quick Start (Recommended VPS Providers)

1. **DigitalOcean** - $6/month (512MB RAM, 1 CPU)
   - Sign up: https://www.digitalocean.com/
   - Create Droplet: Ubuntu 22.04 LTS
   - Choose $6/month plan

2. **Linode** - $5/month (1GB RAM, 1 CPU)
   - Sign up: https://www.linode.com/
   - Create Linode: Ubuntu 22.04 LTS
   - Choose Nanode 1GB plan

3. **AWS Lightsail** - $3.50/month (512MB RAM, 1 CPU)
   - Sign up: https://aws.amazon.com/lightsail/
   - Create instance: Ubuntu 22.04 LTS
   - Choose Nano plan

## Deployment Steps

### Step 1: Get Your VPS

1. Create an account with a VPS provider
2. Create a new server (Ubuntu 22.04 LTS recommended)
3. Note your server IP address and root password
4. SSH into your server:
   ```bash
   ssh root@YOUR_VPS_IP
   ```

### Step 2: Upload Files

**On Windows:**
```bash
upload.bat YOUR_VPS_IP
```

**On Mac/Linux:**
```bash
chmod +x upload.sh
./upload.sh YOUR_VPS_IP
```

### Step 3: Deploy on VPS

SSH into your VPS and run:
```bash
cd /opt/binance-bot
chmod +x deploy.sh
./deploy.sh
```

### Step 4: Configure API Keys

SSH into your VPS and create the .env file:
```bash
cd /opt/binance-bot
nano .env
```

Add your API keys:
```
BINANCE_TESTNET_API_KEY=your_testnet_key
BINANCE_TESTNET_API_SECRET=your_testnet_secret
BINANCE_LIVE_API_KEY=your_live_key
BINANCE_LIVE_API_SECRET=your_live_secret
```

Save and exit (Ctrl+X, Y, Enter)

### Step 5: Start the Bot

```bash
./manage.sh start
```

Enable auto-start on boot:
```bash
systemctl enable binance-bot
systemctl enable binance-dashboard
```

## Management Commands

```bash
cd /opt/binance-bot
./manage.sh start      # Start bot and dashboard
./manage.sh stop       # Stop bot and dashboard
./manage.sh restart    # Restart bot and dashboard
./manage.sh status     # Check service status
./manage.sh logs       # View recent logs
./manage.sh follow     # Follow logs in real-time
./manage.sh kill       # Emergency stop (close all positions)
./manage.sh scan       # Run a single scan
./manage.sh backtest   # Run backtest
./manage.sh update     # Update and restart
```

## Access Dashboard

Once started, access the dashboard at:
```
http://YOUR_VPS_IP:8501
```

## Monitoring

### Check Status
```bash
./manage.sh status
```

### View Logs
```bash
./manage.sh logs
```

### Follow Logs (Real-time)
```bash
./manage.sh follow
```

## Security Tips

1. **Change default SSH port** (optional but recommended):
   ```bash
   nano /etc/ssh/sshd_config
   # Change Port 22 to something else
   systemctl restart sshd
   ```

2. **Use SSH keys instead of password** (recommended):
   ```bash
   # On your local machine
   ssh-keygen -t rsa -b 4096
   ssh-copy-id root@YOUR_VPS_IP
   ```

3. **Configure firewall**:
   ```bash
   ufw allow 22
   ufw allow 8501
   ufw enable
   ```

4. **Set up fail2ban** (optional):
   ```bash
   apt install fail2ban
   ```

## Troubleshooting

### Bot won't start
```bash
./manage.sh status
./manage.sh logs
```

### Dashboard not accessible
- Check if service is running: `./manage.sh status`
- Check firewall: `ufw status`
- Check if port 8501 is open: `netstat -tlnp | grep 8501`

### Out of memory
- Upgrade VPS plan
- Reduce universe_size in config.yaml
- Close other services

## Cost Estimation

- **VPS**: $3-12/month depending on provider
- **Binance fees**: ~0.04% per trade (taker fee)
- **No additional costs**

## Backup Strategy

```bash
# Backup config and .env
cd /opt/binance-bot
tar -czf backup-$(date +%Y%m%d).tar.gz config.yaml .env

# Copy to local machine
scp root@YOUR_VPS_IP:/opt/binance-bot/backup-*.tar.gz ./
```

## Support

If you encounter issues:
1. Check logs: `./manage.sh logs`
2. Check status: `./manage.sh status`
3. Review config.yaml settings
4. Verify API keys in .env

## Important Notes

- ⚠️ Never share your .env file or API keys
- ⚠️ Always test on testnet first
- ⚠️ Start with small amounts
- ⚠️ Monitor the bot regularly
- ⚠️ Keep backups of your config

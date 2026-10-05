# Host the dashboard on Oracle Cloud (Always Free)

Result: `https://your-domain` shows the Streamlit dashboard behind a username/password.
Streamlit listens on localhost only; nginx handles HTTPS and the password.
The trading bot runs as a separate service that you start deliberately.

> The older `ORACLE_DEPLOY.md` / `deploy.sh` serve the dashboard on `http://IP:8501` with **no login**.
> The dashboard can close positions, cancel orders and start auto-trading with your live keys,
> so use this folder instead.

## 1. Oracle console
- Create an **Ubuntu 22.04/24.04** instance. Shape **VM.Standard.A1.Flex** (ARM) or the AMD micro. Save the SSH key.
- **Networking -> VCN -> Security List -> Ingress Rules**: add TCP **80** and **443** from `0.0.0.0/0`. Do **not** open 8501.
- Reserve the public IP so it doesn't change.

## 2. Domain
Create an `A` record (e.g. `bot.yourdomain.com`) pointing at the public IP. HTTPS needs a domain.

## 3. On the server
```bash
ssh ubuntu@<public-ip>
sudo git clone https://github.com/norbekoviskandar-tech/binance-bot.git /opt/binance-bot
sudo chown -R $USER: /opt/binance-bot && cd /opt/binance-bot
DOMAIN=bot.yourdomain.com EMAIL=you@example.com bash oracle/setup.sh
nano .env      # BINANCE_API_KEY / BINANCE_API_SECRET
sudo systemctl restart binance-dashboard
```
(Private repo: clone with a token, or `scp` the files.) The script asks for the dashboard password.

## 4. Run the bot (optional, live trading)
Review `config.yaml` first (`use_testnet`, leverage, risk). Then:
```bash
sudo systemctl enable --now binance-bot
./manage.sh follow        # logs
./manage.sh kill          # STOP file: close positions and exit
```

## Binance API key hygiene
Restrict the key to the server's public IP in Binance API Management, enable only Futures trading
(no withdrawals).

## Updating
`bash oracle/update.sh`

## Troubleshooting
- Can't reach the site: Security List rule for 80/443 missing (console step is separate from the on-server firewall).
- Blank/looping page: websocket blocked; confirm `sudo nginx -t` passes and you used `oracle/nginx.conf`.
- certbot fails: DNS A record not pointing at the server yet; re-run `sudo certbot --nginx -d <domain> --redirect`.
- Logs: `journalctl -u binance-dashboard -f`, `tail -f bot.log`.

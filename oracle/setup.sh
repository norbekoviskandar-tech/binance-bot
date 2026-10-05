#!/usr/bin/env bash
# One-time setup on an Oracle Cloud Ubuntu VM (AMD or ARM).
# Run from the cloned repo:   DOMAIN=bot.example.com EMAIL=you@example.com bash oracle/setup.sh
#
# What you get: dashboard at https://DOMAIN behind a password (nginx basic auth + Let's Encrypt).
# Streamlit itself listens on 127.0.0.1 only, so port 8501 is never exposed.
# The trading bot service is installed but NOT enabled or started; you start it yourself.
set -euo pipefail

DOMAIN="${DOMAIN:?Set DOMAIN, e.g. DOMAIN=bot.example.com bash oracle/setup.sh}"
EMAIL="${EMAIL:-}"
DASH_USER="${DASH_USER:-admin}"
APP_DIR="$(cd "$(dirname "$0")/.." && pwd -P)"
RUN_USER="$(id -un)"
HTPASSWD=/etc/nginx/.binance-htpasswd

echo "==> Packages"
sudo apt-get update -y
sudo apt-get install -y python3 python3-venv python3-pip git nginx apache2-utils \
  certbot python3-certbot-nginx iptables-persistent

echo "==> Python environment in ${APP_DIR}/venv"
cd "$APP_DIR"
[ -d venv ] || python3 -m venv venv
./venv/bin/pip install --upgrade pip
./venv/bin/pip install -r requirements.txt

echo "==> .env"
if [ ! -f .env ]; then
  cp .env.example .env
  echo "Created .env from .env.example. EDIT IT with your keys:  nano ${APP_DIR}/.env"
fi
chmod 600 .env

# manage.sh and the old docs use /opt/binance-bot; keep that path working.
if [ "$APP_DIR" != "/opt/binance-bot" ] && [ ! -e /opt/binance-bot ]; then
  sudo ln -s "$APP_DIR" /opt/binance-bot
fi

echo "==> systemd services"
sudo tee /etc/systemd/system/binance-dashboard.service >/dev/null <<UNIT
[Unit]
Description=Binance Bot Dashboard (Streamlit, localhost only)
After=network.target

[Service]
Type=simple
User=${RUN_USER}
WorkingDirectory=${APP_DIR}
ExecStart=${APP_DIR}/venv/bin/streamlit run dashboard.py --server.port=8501 --server.address=127.0.0.1 --server.headless=true --browser.gatherUsageStats=false
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
UNIT

sudo tee /etc/systemd/system/binance-bot.service >/dev/null <<UNIT
[Unit]
Description=Binance Trading Bot
After=network.target

[Service]
Type=simple
User=${RUN_USER}
WorkingDirectory=${APP_DIR}
ExecStart=${APP_DIR}/venv/bin/python bot.py --live
Restart=always
RestartSec=10
StandardOutput=append:${APP_DIR}/bot.log
StandardError=append:${APP_DIR}/bot.log

[Install]
WantedBy=multi-user.target
UNIT

sudo systemctl daemon-reload
sudo systemctl enable --now binance-dashboard
# Deliberately NOT enabling/starting binance-bot (it trades live money).

echo "==> Dashboard password (user: ${DASH_USER})"
GENERATED_PASS=""
if [ -z "${DASH_PASS:-}" ]; then
  DASH_PASS="$(openssl rand -base64 18 | tr -d '/+=' | cut -c1-16)"
  GENERATED_PASS="$DASH_PASS"
fi
sudo htpasswd -bBc "$HTPASSWD" "$DASH_USER" "$DASH_PASS"

echo "==> nginx for ${DOMAIN}"
sed "s/__DOMAIN__/${DOMAIN}/g" "$APP_DIR/oracle/nginx.conf" | sudo tee /etc/nginx/sites-available/binance-bot >/dev/null
sudo ln -sf /etc/nginx/sites-available/binance-bot /etc/nginx/sites-enabled/binance-bot
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl reload nginx

echo "==> Instance firewall: open 80/443 only (NOT 8501)"
# Oracle's Ubuntu images REJECT everything except SSH by default.
# You must ALSO open 80/443 in the VCN Security List in the Oracle console.
for port in 80 443; do
  sudo iptables -C INPUT -m state --state NEW -p tcp --dport "$port" -j ACCEPT 2>/dev/null \
    || sudo iptables -I INPUT 1 -m state --state NEW -p tcp --dport "$port" -j ACCEPT
done
sudo netfilter-persistent save

echo "==> HTTPS"
if [ -n "$EMAIL" ]; then
  EMAIL_ARGS=(-m "$EMAIL")
else
  EMAIL_ARGS=(--register-unsafely-without-email)   # no expiry notices; renewals still run automatically
fi
sudo certbot --nginx -d "$DOMAIN" "${EMAIL_ARGS[@]}" --agree-tos --redirect --non-interactive \
  || echo "certbot failed (is the DNS name ${DOMAIN} resolving to this server?). Re-run:  sudo certbot --nginx -d ${DOMAIN} --register-unsafely-without-email --agree-tos --redirect"

cat <<MSG

Done.
  Dashboard:  https://${DOMAIN}
  Login:      user ${DASH_USER}   password ${GENERATED_PASS:-<the one you set>}   (SAVE THIS)
  Edit keys:  nano ${APP_DIR}/.env   then   sudo systemctl restart binance-dashboard
  Start bot:  sudo systemctl enable --now binance-bot     (live trading; check config.yaml first)
  Stop bot:   ./manage.sh stop     Kill switch: ./manage.sh kill
  Update:     bash oracle/update.sh
MSG

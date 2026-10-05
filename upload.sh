#!/bin/bash
# Script to upload bot files to VPS

if [ -z "$1" ]; then
    echo "Usage: ./upload.sh <vps-ip>"
    echo "Example: ./upload.sh 123.45.67.89"
    exit 1
fi

VPS_IP=$1
VPS_USER=root

echo "Uploading files to $VPS_USER@$VPS_IP..."

# Create required files on VPS
ssh $VPS_USER@$VPS_IP "mkdir -p /opt/binance-bot"

# Upload bot files
scp bot.py config.yaml requirements.txt dashboard.py scanner.py $VPS_USER@$VPS_IP:/opt/binance-bot/

# Upload .env if it exists
if [ -f .env ]; then
    scp .env $VPS_USER@$VPS_IP:/opt/binance-bot/
    echo ".env file uploaded"
else
    echo "WARNING: .env file not found. You'll need to create it on the VPS."
fi

echo ""
echo "Upload complete!"
echo "Now SSH into the VPS and run: cd /opt/binance-bot && ./deploy.sh"

@echo off
REM Script to upload bot files to VPS (Windows version)

if "%1"=="" (
    echo Usage: upload.bat ^<vps-ip^>
    echo Example: upload.bat 123.45.67.89
    exit /b 1
)

set VPS_IP=%1
set VPS_USER=root

echo Uploading files to %VPS_USER%@%VPS_IP%...

REM Create required files on VPS
plink %VPS_USER%@%VPS_IP% "mkdir -p /opt/binance-bot"

REM Upload bot files
pscp bot.py config.yaml requirements.txt dashboard.py scanner.py %VPS_USER%@%VPS_IP%:/opt/binance-bot/

REM Upload .env if it exists
if exist .env (
    pscp .env %VPS_USER%@%VPS_IP%:/opt/binance-bot/
    echo .env file uploaded
) else (
    echo WARNING: .env file not found. You'll need to create it on the VPS.
)

echo.
echo Upload complete!
echo Now SSH into the VPS and run: cd /opt/binance-bot && ./deploy.sh

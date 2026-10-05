@echo off
REM Script to run bot on Windows in background

echo Starting Binance Trading Bot...
echo.

REM Check if Python is installed
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo ERROR: Python not found. Please install Python 3.11+
    pause
    exit /b 1
)

REM Install dependencies if needed
if not exist venv (
    echo Creating virtual environment...
    python -m venv venv
)

REM Activate virtual environment
call venv\Scripts\activate.bat

REM Install dependencies
echo Installing dependencies...
pip install -r requirements.txt --quiet

REM Check if .env exists
if not exist .env (
    echo WARNING: .env file not found!
    echo Please create .env with your API keys
    echo.
    echo Example .env:
    echo BINANCE_LIVE_API_KEY=your_key
    echo BINANCE_LIVE_API_SECRET=your_secret
    echo.
    pause
    exit /b 1
)

REM Start bot in scan mode first
echo.
echo Starting bot in SCAN mode (no trading)...
python bot.py --scan

echo.
echo Scan complete.
echo.
echo To run in LIVE mode (trading with real money):
echo 1. Edit this file and change --scan to --live
echo 2. Make sure .env has your LIVE API keys
echo 3. Run this script again
echo.
pause

@echo off
REM AUTOMATED SETUP - Just run this and follow the prompts
REM This will set up everything on your computer automatically

echo.
echo ==========================================
echo   BINANCE TRADING BOT - AUTOMATED SETUP
echo ==========================================
echo.
echo This will set up the bot on your computer.
echo It will run as long as your computer is on.
echo.
echo Press any key to continue...
pause >nul

echo.
echo Step 1: Checking Python...
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo Python not found. Installing Python...
    echo Please wait, this may take a few minutes...
    powershell -Command "Invoke-WebRequest -Uri https://www.python.org/ftp/python/3.11.0/python-3.11.0-amd64.exe -OutFile python-installer.exe"
    echo Installing Python...
    python-installer.exe /quiet InstallAllUsers=1 PrependPath=1
    del python-installer.exe
    echo Python installed. Please restart this script.
    pause
    exit /b 1
)
echo Python is installed.

echo.
echo Step 2: Creating virtual environment...
if not exist venv (
    python -m venv venv
    echo Virtual environment created.
) else (
    echo Virtual environment already exists.
)

echo.
echo Step 3: Installing dependencies...
call venv\Scripts\activate.bat
pip install --upgrade pip --quiet
pip install -r requirements.txt --quiet
echo Dependencies installed.

echo.
echo Step 4: Checking for API keys...
if not exist .env (
    echo.
    echo ========================================================
    echo   YOU NEED TO ADD YOUR BINANCE API KEYS
    echo ========================================================
    echo.
    echo I will create the .env file for you.
    echo You need to add your API keys to it.
    echo.
    echo Creating .env file...
    (
        echo # Binance API Keys
        echo BINANCE_TESTNET_API_KEY=your_testnet_key_here
        echo BINANCE_TESTNET_API_SECRET=your_testnet_secret_here
        echo BINANCE_LIVE_API_KEY=your_live_key_here
        echo BINANCE_LIVE_API_SECRET=your_live_secret_here
    ) > .env
    echo .env file created.
    echo.
    echo IMPORTANT: You need to edit .env and add your real API keys.
    echo.
    echo To edit .env:
    echo 1. Right-click on .env file
    echo 2. Select "Open with" then "Notepad"
    echo 3. Replace the placeholder text with your real API keys
    echo 4. Save and close
    echo.
    echo Press any key when you have added your API keys...
    pause >nul
) else (
    echo .env file already exists.
)

echo.
echo Step 5: Testing the bot...
echo Running scan mode (no trading) to test...
python bot.py --scan

echo.
echo ========================================================
echo   SETUP COMPLETE!
echo ========================================================
echo.
echo The bot is now ready to use.
echo.
echo To START the bot:
echo   1. Double-click RUN_BOT.bat
echo.
echo To STOP the bot:
echo   1. Press Ctrl+C in the bot window
echo.
echo To VIEW the dashboard:
echo   1. Double-click DASHBOARD.bat
echo.
echo IMPORTANT:
echo - Keep your computer ON to keep the bot running
echo - The bot will stop if your computer sleeps or shuts down
echo - Check your .env file has correct API keys
echo.
echo Press any key to exit...
pause >nul

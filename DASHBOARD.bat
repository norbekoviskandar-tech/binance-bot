@echo off
echo Starting Dashboard...
echo.

call venv\Scripts\activate.bat

if not exist .env (
    echo WARNING: .env file not found
    echo Dashboard will run in read-only mode
)

echo Dashboard is starting...
echo Open your browser and go to: http://localhost:8501
echo Press Ctrl+C to stop the dashboard.
echo.

python -m streamlit run dashboard.py

echo.
echo Dashboard stopped.
pause

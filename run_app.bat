@echo off
SETLOCAL
REM Ensure script runs from its own directory (avoid System32 issue)
cd /d "%~dp0"

REM Move into backend folder
cd backend

REM Create venv if missing
IF NOT EXIST venv (
  echo Creating virtual environment in %CD%\venv...
  python -m venv venv
  IF %ERRORLEVEL% NEQ 0 (
    echo Failed to create virtual environment. Ensure Python 3.10+ is installed and on PATH.
    pause
    exit /b 1
  )
)

REM Activate venv
call "%~dp0backend\venv\Scripts\activate.bat"

echo Installing requirements (if not already)...
python -m pip install --upgrade pip --user
pip install --upgrade -r requirements.txt

echo Starting AsphaltTracker...
start "" python run_app.py

echo App should open in your default browser. If it does not, open http://127.0.0.1:8000
pause

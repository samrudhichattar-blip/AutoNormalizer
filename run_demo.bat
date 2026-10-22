@echo off
REM Double-click to run AutoNormalizer Plus (Windows). First run installs the packages.
cd /d %~dp0
if not exist .venv ( python -m venv .venv )
call .venv\Scripts\activate.bat
pip install -r requirements.txt
start "" cmd /c "timeout /t 4 >nul & start http://127.0.0.1:8000"
python -m uvicorn backend.api.demo_app:app --reload

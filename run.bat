@echo off
REM ClaimShield (Windows).  Usage:  run [setup|app|offline|demo|doctor|check|test|build-kb|snapshot|samples|eval|credits|models]
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0"
set CMD=%1
if "%CMD%"=="" set CMD=app

if /I "%CMD%"=="setup" (
  python -m venv venv || goto :err
  call venv\Scripts\activate.bat
  python -m pip install --upgrade pip >nul
  pip install -r requirements.txt || goto :err
  if not exist .env ( copy .env.example .env >nul & echo Created .env - add your API keys, or leave it empty for offline mode )
  python scripts\doctor.py
  echo.
  echo Ready. Start the app with:  run app
  goto :eof
)
if exist venv\Scripts\activate.bat call venv\Scripts\activate.bat
if /I "%CMD%"=="app"      ( python -m streamlit run app\ui.py & goto :eof )
if /I "%CMD%"=="offline"  ( set LLM_MODE=off& python -m streamlit run app\ui.py & goto :eof )
if /I "%CMD%"=="demo"     ( set LLM_MODE=replay& python -m streamlit run app\ui.py & goto :eof )
if /I "%CMD%"=="doctor"   ( python scripts\doctor.py & goto :eof )
if /I "%CMD%"=="check"    ( python scripts\doctor.py --live & goto :eof )
if /I "%CMD%"=="models"   ( python scripts\doctor.py --models %2 & goto :eof )
if /I "%CMD%"=="test"     ( python -m pytest -q & goto :eof )
if /I "%CMD%"=="build-kb" ( python scripts\build_kb.py %2 %3 %4 %5 & goto :eof )
if /I "%CMD%"=="snapshot" ( python scripts\demo.py snapshot %2 %3 %4 & goto :eof )
if /I "%CMD%"=="samples"  ( python scripts\make_scenarios.py & goto :eof )
if /I "%CMD%"=="eval"     ( python eval\run_eval.py --engines rules lean single --max-usd 8 & goto :eof )
if /I "%CMD%"=="credits"  ( python scripts\demo.py credits & goto :eof )
echo Usage: run [setup^|app^|offline^|demo^|doctor^|check^|test^|build-kb^|snapshot^|samples^|eval^|credits^|models]
goto :eof
:err
echo Setup failed. Check Python 3.10-3.12 is installed and on PATH:  python --version
exit /b 1

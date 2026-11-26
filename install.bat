@echo off
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (py -3.12 -m venv .venv) else (python -m venv .venv)
if errorlevel 1 goto fail
.venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 goto fail
echo Installation complete. Run start.bat.
pause
exit /b 0
:fail
echo Installation failed. Install Python 3.10-3.13 and retry.
pause
exit /b 1

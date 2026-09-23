@echo off
setlocal
chcp 65001 > nul
title QC system launcher

set "PROJECT_ROOT=%~dp0"
set "BACKEND_DIR=%PROJECT_ROOT%backend"
set "PYTHON_EXE=%PROJECT_ROOT%.venv\Scripts\python.exe"
if not exist "%PYTHON_EXE%" (
    echo WARNING: project venv not found at "%PROJECT_ROOT%.venv\Scripts\python.exe"
    echo Falling back to system python.
    set "PYTHON_EXE=python"
)

if not exist "%BACKEND_DIR%\main.py" (
    echo ERROR: backend entrypoint not found.
    pause
    exit /b 1
)
if not exist "%BACKEND_DIR%\static\index.html" (
    echo ERROR: frontend build not found.
    pause
    exit /b 1
)

echo Starting QC system on http://10.3.200.61:8010 ...

set "PORT=8010"
start "qc-backend" /D "%BACKEND_DIR%" cmd.exe /k ""%PYTHON_EXE%" main.py"

timeout /t 4 /nobreak > nul
start "" "http://10.3.200.61:8010"
echo QC backend and web UI started.
echo.
echo This window stays open so startup errors remain visible.
pause > nul
endlocal

@echo off
setlocal EnableExtensions
chcp 65001 > nul
title QC Launcher

cd /d "%~dp0"
set "PROJECT_ROOT=%CD%"
set "BACKEND_DIR=%PROJECT_ROOT%\backend"
set "FRONTEND_DIR=%PROJECT_ROOT%\frontend"
set "PYTHON_EXE=%PROJECT_ROOT%\.venv\Scripts\python.exe"
set "ENSURE_ENV=%PROJECT_ROOT%\scripts\ensure-env.ps1"

if not exist "%ENSURE_ENV%" (
    echo ERROR: environment check script not found: %ENSURE_ENV%
    pause
    exit /b 1
)
echo Checking runtime environment...
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%ENSURE_ENV%"
if errorlevel 1 (
    echo ERROR: environment check failed. Backend will not start.
    pause
    exit /b 1
)
if not exist "%BACKEND_DIR%\main.py" (
    echo ERROR: backend entrypoint not found.
    pause
    exit /b 1
)

echo Building frontend into backend\static ...
pushd "%FRONTEND_DIR%"
call npm.cmd run build
set "BUILD_ERROR=%ERRORLEVEL%"
popd
if not "%BUILD_ERROR%"=="0" (
    echo ERROR: frontend build failed. Backend will not start.
    pause
    exit /b 1
)
if not exist "%BACKEND_DIR%\static\index.html" (
    echo ERROR: frontend build output not found.
    pause
    exit /b 1
)

echo.
echo QC URL: http://10.3.200.61:8010
echo.
netstat -ano | findstr /r /c:":8010 .*LISTENING" >nul
if "%ERRORLEVEL%"=="0" (
    echo Port 8010 is already running. Opening the page only.
) else (
    echo Using Python: %PYTHON_EXE%
    echo Starting QC on http://10.3.200.61:8010 ...
    start "qc-backend" /D "%BACKEND_DIR%" cmd.exe /k ""%PYTHON_EXE%" main.py"
    timeout /t 4 /nobreak > nul
)
start "" "http://10.3.200.61:8010"
echo QC page: http://10.3.200.61:8010
echo.
echo This window stays open so startup errors remain visible.
pause > nul
endlocal

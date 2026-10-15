@echo off
setlocal EnableExtensions
chcp 65001 > nul
title Build Frontend

cd /d "%~dp0"
set "PROJECT_ROOT=%CD%"
set "ENSURE_ENV=%PROJECT_ROOT%\scripts\ensure-env.ps1"
if not exist "%ENSURE_ENV%" (
    echo ERROR: environment check script not found: %ENSURE_ENV%
    pause
    exit /b 1
)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%ENSURE_ENV%"
if errorlevel 1 (
    echo ERROR: environment check failed. Build was not started.
    pause
    exit /b 1
)
echo Building frontend...
cd /d "%PROJECT_ROOT%\frontend"
call npm.cmd run build

if not "%ERRORLEVEL%"=="0" (
    echo ERROR: frontend build failed.
    pause
    exit /b 1
)
echo Frontend build completed: backend\static
pause
endlocal

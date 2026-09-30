@echo off
setlocal EnableExtensions
chcp 65001 > nul
title QC 启动器

set "PROJECT_ROOT=%~dp0"
set "BACKEND_DIR=%PROJECT_ROOT%backend"
set "FRONTEND_DIR=%PROJECT_ROOT%frontend"
set "PYTHON_EXE=%PROJECT_ROOT%.venv\Scripts\python.exe"
if not exist "%PYTHON_EXE%" (
    set "PYTHON_EXE=%PROJECT_ROOT%..\海外AI问题案例审核\.venv\Scripts\python.exe"
)
if not exist "%PYTHON_EXE%" (
    echo ERROR: Python venv not found.
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
if exist "C:\Program Files\nodejs\npm.cmd" (
    call "C:\Program Files\nodejs\npm.cmd" run build
) else if exist "D:\Nodejs\npm.cmd" (
    call "D:\Nodejs\npm.cmd" run build
) else (
    call npm.cmd run build
)
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
echo QC 系统地址: http://10.3.200.61:8010
echo.

netstat -ano | findstr /r /c:":8010 .*LISTENING" >nul
if "%ERRORLEVEL%"=="0" (
    echo 8010 已在运行，不再重复启动后端，只打开页面。
) else (
    echo Using Python: %PYTHON_EXE%
    echo Starting QC on http://10.3.200.61:8010 ...
    start "qc-backend" /D "%BACKEND_DIR%" cmd.exe /k ""%PYTHON_EXE%" main.py"
    timeout /t 4 /nobreak > nul
)

start "" "http://10.3.200.61:8010"
echo QC 系统页面: http://10.3.200.61:8010
echo.
echo This window stays open so startup errors remain visible.
pause > nul
endlocal

@echo off
chcp 65001 > nul
title 构建前端

echo 正在构建前端界面...
cd /d E:\qc_system\frontend
call "C:\Program Files\nodejs\npm.cmd" run build

echo.
if %ERRORLEVEL% == 0 (
    echo 构建完成！前端已更新到 backend\static 目录。
) else (
    echo 构建失败，请查看错误信息。
)
pause

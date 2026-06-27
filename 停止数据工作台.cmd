@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Stopping Quant Data workspace...
docker compose down
pause

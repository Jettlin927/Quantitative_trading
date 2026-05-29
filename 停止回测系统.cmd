@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Stopping Quantitative Trading workspace...
docker compose down
pause

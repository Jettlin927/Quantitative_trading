@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "API_PORT=18000"
set "FRONTEND_PORT=15173"
if exist ".env" (
  for /f "usebackq tokens=1,* delims==" %%A in (".env") do (
    if /i "%%A"=="API_PORT" set "API_PORT=%%B"
    if /i "%%A"=="FRONTEND_PORT" set "FRONTEND_PORT=%%B"
  )
)
echo Rebuilding and starting Quantitative Trading workspace...
docker compose up -d --build
if errorlevel 1 (
  echo.
  echo Docker failed to rebuild/start. Please make sure Docker Desktop is running.
  pause
  exit /b 1
)
echo.
echo Frontend: http://localhost:%FRONTEND_PORT%
echo API docs: http://localhost:%API_PORT%/docs
start "" "http://localhost:%FRONTEND_PORT%"
pause

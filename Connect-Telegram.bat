@echo off
setlocal EnableExtensions
title Inbound Surveillance - Connect Telegram

set "TOKEN=8987540090:AAEntu6IaceRsrnNl0Eow7ZrOYHBR90FkZU"
set "DIR=%APPDATA%\Inbound Surveillance"
set "CFG=%DIR%\config.yaml"

set "APP=%LOCALAPPDATA%\Inbound Surveillance\Inbound Surveillance.exe"
if not exist "%APP%" set "APP=%ProgramFiles%\Inbound Surveillance\Inbound Surveillance.exe"
if not exist "%APP%" set "APP=%ProgramFiles(x86)%\Inbound Surveillance\Inbound Surveillance.exe"

echo ============================================================
echo   Inbound Surveillance - Telegram connection setup
echo ============================================================
echo.
echo Closing Inbound Surveillance...
taskkill /IM "Inbound Surveillance.exe" /F >nul 2>&1
taskkill /IM "inbound-engine.exe" /F >nul 2>&1
timeout /t 3 /nobreak >nul

if not exist "%DIR%" mkdir "%DIR%" >nul 2>&1

echo Writing the Telegram bot token into your settings...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$q=[char]34; $t='telegram_bot_token: '+$q+$env:TOKEN+$q; $p=$env:CFG; $text=[IO.File]::ReadAllText($p); if($text -match '(?m)^\s*telegram_bot_token\s*:.*$'){ $text=[regex]::Replace($text,'(?m)^\s*telegram_bot_token\s*:.*$',$t) } else { $nl=[Environment]::NewLine; if(-not $text.EndsWith($nl)){ $text+=$nl }; $text+=$t+$nl }; [IO.File]::WriteAllText($p,$text,(New-Object System.Text.UTF8Encoding($false)))" >nul 2>&1

rem --- Self-check: if the token is still not there, reset settings so the app
rem --- re-creates a complete config (which already contains the built-in bot).
findstr /C:"telegram_bot_token" "%CFG%" >nul 2>&1
if errorlevel 1 (
  echo Token could not be added automatically. Restoring default settings...
  if exist "%CFG%" copy /Y "%CFG%" "%CFG%.backup" >nul 2>&1
  del /F /Q "%CFG%" >nul 2>&1
)

echo.
echo Starting Inbound Surveillance...
if exist "%APP%" (
  start "" "%APP%"
) else (
  echo NOTE: Could not find the app automatically. Please open it yourself.
)

echo.
echo ============================================================
echo   Done. Finish inside the app (2 taps):
echo   1. Open the "Integrations" tab.
echo   2. Click "Connect Telegram".
echo   3. In Telegram, tap "Start".
echo   4. Click "Capture and Send Test" to confirm.
echo ============================================================
echo.
echo ---- Diagnostic (please screenshot this part) ----
findstr /C:"telegram_bot_token" "%CFG%" >nul 2>&1 && echo Telegram token in settings: YES || echo Telegram token in settings: NO
if exist "%APPDATA%\Inbound Surveillance\logs\startup.log" (
  findstr /C:"INBOUND_BOOT" "%APPDATA%\Inbound Surveillance\logs\startup.log"
) else (
  echo No engine log found yet.
)
echo --------------------------------------------------
echo.
pause

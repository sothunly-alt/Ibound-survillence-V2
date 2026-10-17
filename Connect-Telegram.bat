@echo off
setlocal EnableExtensions
title Inbound Surveillance - Connect Telegram

rem ================================================================
rem  One-click Telegram setup for an already-installed Inbound
rem  Surveillance app (Windows). It:
rem    1. stops the app
rem   2. writes the built-in Telegram bot token into settings
rem   3. restarts the app and waits for the camera engine
rem   4. opens Telegram and waits for the customer to tap START
rem   5. saves the linked chat so alerts are delivered
rem ============================================================

set "TOKEN=8987540090:AAEntu6IaceRsrnNl0Eow7ZrOYHBR90FkZU"
set "DATA=%APPDATA%\Inbound Surveillance"
set "CFG=%DATA%\config.yaml"
set "LOG=%DATA%\logs\startup.log"
set "PORT=8765"
set "APP=%LOCALAPPDATA%\Inbound Surveillance\Inbound Surveillance.exe"
if not exist "%APP%" set "APP=%ProgramFiles%\Inbound Surveillance\Inbound Surveillance.exe"
if not exist "%APP%" set "APP=%ProgramFiles(x86)%\Inbound Surveillance\Inbound Surveillance.exe"

echo ============================================================
echo    Inbound Surveillance  -  Connect Telegram
echo ============================================================
echo.

echo [1/5] Closing Inbound Surveillance...
taskkill /IM "Inbound Surveillance.exe" /F >nul 2>&1
taskkill /IM "inbound-engine.exe" /F >nul 2>&1
timeout /t 3 /nobreak >nul

if not exist "%DATA%" mkdir "%DATA%" >nul 2>&1

echo [2/5] Saving the Telegram bot token...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$q=[char]34;$t='telegram_bot_token: '+$q+$env:TOKEN+$q;$p=$env:CFG;if(Test-Path $p){$x=[IO.File]::ReadAllText($p);if($x -match '(?m)^\s*telegram_bot_token\s*:.*$'){$x=[regex]::Replace($x,'(?m)^\s*telegram_bot_token\s*:.*$',$t)}else{$x=$x.TrimEnd()+[Environment]::NewLine+$t}}else{$x=$t};[IO.File]::WriteAllText($p,$x,(New-Object System.Text.UTF8Encoding($false)))" >nul 2>&1

findstr /C:"telegram_bot_token" "%CFG%" >nul 2>&1
if errorlevel 1 (
  echo    Could not write the token; restoring default settings so the app recreates them...
  if exist "%CFG%" copy /Y "%CFG%" "%CFG%.backup" >nul 2>&1
  del /F /Q "%CFG%" >nul 2>&1
)

echo [3/5] Starting Inbound Surveillance...
if exist "%APP%" (
  start "" "%APP%"
) else (
  echo    Could not find the app automatically. Please open it, then press any key.
  pause >nul
)

echo [4/5] Waiting for the camera engine (this can take ~30s)...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$p='http://127.0.0.1:'+$env:PORT;for($i=0;$i -lt 120;$i++){try{$r=Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 ($p+'/api/public-config');if($r.StatusCode -eq 200){exit 0}}catch{};Start-Sleep -Seconds 2};exit 1"
if errorlevel 1 (
  echo    The engine did not start. Make sure the Inbound Surveillance window is open.
  goto :manual
)

echo [5/5] Opening Telegram - tap START in the Telegram chat, then wait...
powershell -NoProfile -ExecutionPolicy Bypass -Command "& {$ErrorActionPreference='SilentlyContinue';$p='http://127.0.0.1:'+$env:PORT;$body=@{user_id='local-operator';display_name='Operator'}|ConvertTo-Json;$r=Invoke-RestMethod -Method Post -Uri ($p+'/api/telegram/begin-link') -ContentType 'application/json' -Body $body;if(-not $r.ok -and -not $r.code){Write-Host ('    ERROR: '+$r.error);exit 2};if($r.deep_link){Start-Process $r.deep_link};Write-Host '    Telegram was opened. Tap START there and keep this window open...';for($i=0;$i -lt 150;$i++){Start-Sleep -Seconds 2;try{$s=Invoke-RestMethod -Method Get -Uri ($p+'/api/telegram/status?user_id=local-operator')}catch{$s=$null};if($s -and $s.telegram_chat_id){Write-Host ('    Connected. Chat id: '+$s.telegram_chat_id);exit 0}};Write-Host '    Did not see START in time.';exit 3}"
set "RC=%errorlevel%"

if "%RC%"=="0" (
  echo.
  echo ============================================================
  echo    SUCCESS - Telegram is connected.
  echo    Alerts will now arrive in your Telegram chat.
  echo ============================================================
  goto :done
)

:manual
echo.
echo ============================================================
echo    Finish connecting in two taps:
echo      1. Open Telegram and open  @Inbound_Surveillance_bot
echo      2. Tap START  (or send:  /start)
echo    Then, in the app, go to Integrations
echo    and click "Connect Telegram".
echo ============================================================

:done
echo.
echo ---- Diagnostic (screenshot this if you need help) ----
findstr /C:"telegram_bot_token" "%CFG%" >nul 2>&1 && echo Settings token: YES || echo Settings token: NO
if exist "%LOG%" (
  findstr /C:"INBOUND_SERVER_READY" "%LOG%" >nul 2>&1 && echo Engine started: YES || echo Engine started: NO
) else (
  echo Engine log: not found
)
echo ------------------------------------------------------
echo.
pause

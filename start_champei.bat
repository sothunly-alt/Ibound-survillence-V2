@echo off
title Champei Spa Intelligence
echo Starting Champei Spa Intelligence System...

cd /d "%~dp0"

:: Activate virtual environment if present
if exist edge\.venv\Scripts\activate.bat (
    call edge\.venv\Scripts\activate.bat
) else if exist venv\Scripts\activate.bat (
    call venv\Scripts\activate.bat
) else if exist .venv\Scripts\activate.bat (
    call .venv\Scripts\activate.bat
)

:: Run Champei daemon
cd edge
:: Local single-machine install: skip the cloud login gate. The hub binds to
:: 127.0.0.1 only, so this never exposes the console off this PC.
set HUB_NO_AUTH=1
python run_champei.py

pause


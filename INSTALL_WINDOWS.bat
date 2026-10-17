@echo off
setlocal enabledelayedexpansion
title Champei Spa Intelligence - Automatic Setup
color 0A

echo ====================================================================
echo      CHAMPEI SPA INTELLIGENCE SYSTEM - 1-CLICK AUTOMATIC SETUP
echo ====================================================================
echo.

cd /d "%~dp0"

:: 1. Check for Python
python --version >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    py --version >nul 2>&1
    if %ERRORLEVEL% NEQ 0 (
        color 0C
        echo [ERROR] Python is not installed on this computer!
        echo.
        echo Please download and install Python 3.10, 3.11, or 3.12:
        echo 1. Go to: https://www.python.org/downloads/
        echo 2. IMPORTANT: Check the box "Add Python to PATH" during installation.
        echo 3. Run this INSTALL_WINDOWS.bat again after installing Python.
        echo.
        pause
        exit /b 1
    ) else (
        set "PY_CMD=py"
    )
) else (
    set "PY_CMD=python"
)

echo [*] Python detected:
%PY_CMD% --version
echo.

:: 1b. Verify Python version is 3.10 - 3.12 (torch/ultralytics wheels)
for /f "tokens=1,2" %%a in ('%PY_CMD% -c "import sys;print(sys.version_info[0],sys.version_info[1])"') do (
    set "PY_MAJOR=%%a"
    set "PY_MINOR=%%b"
)
if !PY_MAJOR! LSS 3 (
    set "PY_BAD=1"
)
if !PY_MAJOR! EQU 3 if !PY_MINOR! LSS 10 set "PY_BAD=1"
if !PY_MAJOR! EQU 3 if !PY_MINOR! GTR 12 set "PY_BAD=1"
if defined PY_BAD (
    color 0E
    echo [WARN] Detected Python !PY_MAJOR!.!PY_MINOR!. This system supports Python 3.10 - 3.12.
    echo        Newer/older versions may lack prebuilt AI wheels and the install may fail.
    echo        Recommended: install Python 3.12 from https://www.python.org/downloads/
    echo        ^(check "Add Python to PATH"^), then run this installer again.
    echo.
    pause
    color 0A
)

:: 2. Create Virtual Environment
echo [*] Setting up isolated application environment...
if not exist "edge\.venv" (
    %PY_CMD% -m venv edge\.venv
    if %ERRORLEVEL% NEQ 0 (
        echo [ERROR] Failed to create virtual environment.
        pause
        exit /b 1
    )
)
echo [OK] Virtual environment ready.
echo.

:: 3. Install Requirements
echo [*] Installing AI and vision components (this takes 1-2 minutes)...
call edge\.venv\Scripts\activate.bat
python -m pip install --upgrade pip --quiet
python -m pip install -r edge\requirements.txt
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Failed to install requirements. Please check your internet connection.
    pause
    exit /b 1
)
echo [OK] All dependencies successfully installed.
echo.

:: 4. Deploy Customer Config (Champei Spa)
echo [*] Deploying customer configuration...
if not exist "edge\config.yaml" (
    if exist "edge\config.champei.yaml" (
        copy /Y "edge\config.champei.yaml" "edge\config.yaml" >nul
        echo [OK] Customer config deployed to edge\config.yaml
    ) else (
        echo [WARN] edge\config.champei.yaml not found; using default config.example.yaml.
    )
) else (
    echo [INFO] edge\config.yaml already exists; keeping existing configuration.
)
echo.

:: 5. Verify Installation
echo [*] Running diagnostic check...
python edge\verify_customer_install.py
echo.

:: 6. Create Desktop Shortcut
echo [*] Creating Desktop Shortcut...
set "TARGET_BAT=%~dp0start_champei.bat"
set "SHORTCUT_PATH=%USERPROFILE%\Desktop\Champei Spa Intelligence.lnk"
set "ICON_PATH=%~dp0favicon.svg"

powershell -Command "$ws = New-Object -ComObject WScript.Shell; $s = $ws.CreateShortcut('%SHORTCUT_PATH%'); $s.TargetPath = '%TARGET_BAT%'; $s.WorkingDirectory = '%~dp0'; $s.Save()"

if exist "%SHORTCUT_PATH%" (
    echo [OK] Shortcut created on Desktop: "Champei Spa Intelligence"
) else (
    echo [INFO] You can launch the system using start_champei.bat in this folder.
)

echo.
echo ====================================================================
echo   SUCCESS! Installation Complete.
echo   You can now start the system by double-clicking:
echo   - "Champei Spa Intelligence" on your Desktop, OR
echo   - "start_champei.bat" in this folder.
echo ====================================================================
echo.
pause

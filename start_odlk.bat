@echo off
rem ODLK1 Voxel runner (silent background execution with system tray icon)
cd /d "%~dp0"
where pythonw >nul 2>&1
if %errorlevel% equ 0 (
    start "" pythonw app.py %*
    exit /b 0
)
where pyw >nul 2>&1
if %errorlevel% equ 0 (
    start "" pyw app.py %*
    exit /b 0
)
where python >nul 2>&1
if %errorlevel% equ 0 (
    start "" python app.py %*
    exit /b 0
)
start "" py app.py %*

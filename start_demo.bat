@echo off
rem ODLK1 Voxel demo runner (silent background execution with system tray icon)
cd /d "%~dp0"
where pythonw >nul 2>&1
if %errorlevel% equ 0 (
    start "" pythonw app.py --demo %*
) else (
    start "" python app.py --demo %*
)

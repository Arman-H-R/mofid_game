@echo off
rem Market Pulse launcher (Windows). Examples:
rem   run.bat --windowed --simulate
rem   run.bat
rem Always uses this folder's own .venv. If .venv is missing, or was copied from
rem another PC (error "No Python at 'C:\Users\...\python.exe'"), or has plain
rem pygame instead of pygame-ce, it is made again with this PC's Python and the
rem requirements are installed.

setlocal
cd /d "%~dp0"
set PY=.venv\Scripts\python.exe

"%PY%" -c "import pygame, sys; sys.exit(0 if getattr(pygame, 'IS_CE', False) else 1)" >nul 2>&1
if not errorlevel 1 goto run

echo .venv is missing or does not work on this PC; making it again...
if exist .venv rmdir /s /q .venv
py -3.10 -m venv .venv 2>nul || python -m venv .venv
if errorlevel 1 (
    echo Could not make .venv: install Python 3.10 or newer from python.org
    exit /b 1
)
"%PY%" -m pip install -r requirements.txt
if errorlevel 1 (
    echo pip install failed: check the internet connection and run run.bat again
    exit /b 1
)

:run
"%PY%" main.py %*
endlocal

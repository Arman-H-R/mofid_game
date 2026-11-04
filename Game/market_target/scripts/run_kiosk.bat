@echo off
rem Market Pulse kiosk launcher (Windows).
rem Starts the game fullscreen and restarts it if it ever exits with an error.
rem The operator quits for real with Esc pressed twice (exit code 0).
rem Put a shortcut to this file in shell:startup to start with Windows.

setlocal
cd /d "%~dp0.."
set PY=.venv\Scripts\python.exe
if not exist "%PY%" set PY=python

:loop
"%PY%" main.py %*
set CODE=%ERRORLEVEL%
if "%CODE%"=="0" goto end
echo [%date% %time%] Market Pulse exited with code %CODE%; restarting in 3 seconds >> logs\kiosk_restarts.log
timeout /t 3 /nobreak > nul
goto loop

:end
endlocal

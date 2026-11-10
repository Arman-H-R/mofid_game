@echo off
rem Sensor test: camera alone, LiDAR alone, or both (close the game first).
cd /d "%~dp0"
.venv\Scripts\python.exe sensortest.py %*

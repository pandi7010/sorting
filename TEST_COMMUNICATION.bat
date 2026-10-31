@echo off
title ESP32 COM3 Communication Live Test
cd /d "%~dp0"
REM Python: the deployment package's own .venv (..\.venv) if present, otherwise as before
set "PY=%~dp0..\.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=C:\Users\User\AppData\Local\Programs\Python\Python312\python.exe"
if not exist "%PY%" set "PY=python"
"%PY%" check_esp32_comm.py
echo.
pause

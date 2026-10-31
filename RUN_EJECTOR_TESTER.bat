@echo off
title ESP32 63-CH Ejector Diagnostics
cd /d "%~dp0"
REM Python: the deployment package's own .venv (..\.venv) if present, otherwise as before
set "PY=%~dp0..\.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=C:\Users\User\AppData\Local\Programs\Python\Python312\python.exe"
if not exist "%PY%" set "PY=python"
"%PY%" gui_esp32_ejector_tester.py
pause

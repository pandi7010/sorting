@echo off
title LAMBTON 63-CHANNEL COLOUR SORTER
cd /d "%~dp0"
echo Starting LAMBTON Colour Sorter...
REM Python: the deployment package's own .venv (..\.venv) if present, otherwise as before
set "PY=%~dp0..\.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"
"%PY%" LAMTON_LINE_NEW_ESP32.py
pause

@echo off
title LAMTON - 63-CH Ejector Test Panel
cd /d "%~dp0"
REM Close the sorter program and the Arduino Serial Monitor first (one program per COM port).
REM Options:  RUN_EJECTOR_TEST_PANEL.bat --port COM13     RUN_EJECTOR_TEST_PANEL.bat --sim
REM Python: the deployment package's own .venv (..\.venv) if present, otherwise as before
set "PY=%~dp0..\.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=C:\Users\User\AppData\Local\Programs\Python\Python312\python.exe"
if not exist "%PY%" set "PY=python"
"%PY%" ejector_test_panel.py %*
if errorlevel 1 pause

@echo off
setlocal
title LAMTON V2 - COLOUR SORTER
REM Starts the sorter from this package: program in app\, Python in .venv\ (made by INSTALL.bat).
REM Works from any folder (e.g. C:\LAMTON_V2). Close the ejector test kit / Arduino Serial Monitor first.
cd /d "%~dp0app"
set "PY=%~dp0.venv\Scripts\python.exe"
if not exist "%PY%" (
    echo.
    echo  The Python environment is missing. Run INSTALL.bat first.
    echo.
    pause
    exit /b 1
)
echo Starting LAMTON V2 colour sorter from %CD% ...
"%PY%" LAMTON_LINE_NEW_ESP32.py
if errorlevel 1 (
    echo.
    echo  The sorter closed with an error or "Access Denied" - see the messages above
    echo  and run SYSTEM_CHECK.bat.
    pause
)

@echo off
setlocal
title LAMTON - 63-CH Ejector Test Panel
cd /d "%~dp0"
REM ------------------------------------------------------------------
REM  Starts the 63-channel ejector test panel from this folder.
REM  Options:  RUN_EJECTOR_TEST_PANEL.bat --port COM13
REM            RUN_EJECTOR_TEST_PANEL.bat --sim      (practice, no ESP32)
REM  Close the sorter program and the Arduino Serial Monitor first:
REM  only ONE program can use a COM port.
REM
REM  Python is found automatically. A PC can have several Pythons; the first
REM  one that already has PyQt5 + pyserial is used. Order tried:
REM  python, py -3, python3, then every Python the "py" launcher knows.
REM ------------------------------------------------------------------
set "PY="
set "FIRSTPY="
call :try python
call :try py -3
call :try python3
if not defined PY (
    for /f "tokens=1,2,*" %%a in ('py -0p 2^>nul') do call :pyline "%%b" "%%c"
)
if defined PY goto :start
if defined FIRSTPY goto :nopackages
goto :nopython

REM ---- :try <command...>  real Python 3.8+ ? (also skips the Microsoft Store placeholder)
:try
if defined PY exit /b 0
%* -c "import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)" >nul 2>&1
if errorlevel 1 exit /b 0
if not defined FIRSTPY set "FIRSTPY=%*"
%* -c "import PyQt5.QtWidgets, serial.tools.list_ports" >nul 2>&1
if errorlevel 1 exit /b 0
set "PY=%*"
exit /b 0

:pyline
REM "py -0p" lines: " -V:3.10  C:\path\python.exe" or " -V:3.14 *  C:\path\python.exe" (* = default)
if "%~1"=="*" (call :trypath "%~2") else if "%~2"=="" (call :trypath "%~1") else (call :trypath "%~1 %~2")
exit /b 0

:trypath
if defined PY exit /b 0
if "%~1"=="" exit /b 0
if not exist "%~1" exit /b 0
call :try "%~1"
exit /b 0

:start
%PY% -c "import sys; print('Python', sys.version.split()[0], 'with PyQt5 + pyserial found')"
echo   (command: %PY%)
echo Starting the ejector test panel ...
%PY% ejector_test_panel.py %*
if errorlevel 1 (
    echo.
    echo The panel stopped with an error - see the messages above.
    pause
)
exit /b 0

:nopython
echo.
echo  ERROR: Python 3.8 or newer was not found on this PC.
echo.
echo  Install Python 3.10 or newer from https://www.python.org/downloads/windows/
echo  During installation tick  "Add python.exe to PATH".
echo  Then run this file again.
echo.
pause
exit /b 1

:nopackages
echo.
echo  ERROR: Python was found, but PyQt5 and/or pyserial are not installed:
%FIRSTPY% --version
echo.
echo  Install them with this command (Internet needed), then run this file again:
echo.
echo      %FIRSTPY% -m pip install -r "%~dp0requirements_test_panel.txt"
echo.
pause
exit /b 1

@echo off
setlocal
title LAMTON V2 - SYSTEM CHECK (read-only)
REM READ-ONLY: prints PASS / WARN / FAIL. Changes no setting, no camera, no board.
REM (The disk speed test writes one temporary 256 MB file and deletes it; skip it with --no-disk-speed.)
REM Close the sorter program first, otherwise its COM ports show as BUSY.
cd /d "%~dp0app"
set "PY=%~dp0.venv\Scripts\python.exe"
if exist "%PY%" goto :run
echo  NOTE: INSTALL.bat has not been run yet - checking with any Python found (packages will show FAIL).
set "PY="
py -3 -c "import sys" >nul 2>&1 && set "PY=py -3"
if not defined PY python -c "import sys" >nul 2>&1 && set "PY=python"
if not defined PY (
    echo.
    echo  FAIL: no Python found. Install Python 3.12 from drivers\python-3.12.10-amd64.exe
    echo        ^(tick "Add python.exe to PATH"^), then run INSTALL.bat.
    echo.
    pause
    exit /b 1
)
:run
REM PY is either the .venv python.exe (a path, quoted) or a command like "py -3" (not quoted)
if exist "%PY%" ("%PY%" tools\system_check.py %*) else (%PY% tools\system_check.py %*)
echo.
pause

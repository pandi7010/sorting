@echo off
setlocal
title LAMTON V2 - download packages for an OFFLINE install
cd /d "%~dp0"
REM ------------------------------------------------------------------------------------------
REM  Run this on ANY Windows PC WITH internet and Python 3.8+ (it can be a different PC).
REM  It downloads every package (sorter + CNN, CPU only, about 350 MB) for the NEW PC's Python
REM  version into the folder  wheels\
REM  Then copy the whole package (with wheels\) to the new PC: INSTALL.bat installs from wheels\
REM  without internet.
REM
REM    MAKE_OFFLINE_WHEELS.bat          Python 3.12 on the new PC
REM    MAKE_OFFLINE_WHEELS.bat 3.11     another Python version on the new PC (3.10 - 3.13)
REM ------------------------------------------------------------------------------------------
set "PYV=%~1"
if "%PYV%"=="" set "PYV=3.12"
set "PY="
py -3 -c "import sys" >nul 2>&1 && set "PY=py -3"
if not defined PY python -c "import sys" >nul 2>&1 && set "PY=python"
if not defined PY (
    echo ERROR: Python not found on this PC.
    pause
    exit /b 1
)
set "OPT=--only-binary=:all: --platform win_amd64 --python-version %PYV% -d wheels"
echo Downloading for Python %PYV% (64-bit Windows) ...
%PY% -m pip download %OPT% -r requirements.txt -r requirements_cnn.txt -c constraints.txt
if errorlevel 1 goto :fail
echo.
echo DONE. Copy the package folder INCLUDING wheels\ to the new PC and run INSTALL.bat there.
echo Install the same Python version (%PYV%) on the new PC.
pause
exit /b 0
:fail
echo.
echo ERROR: download failed (see above).
pause
exit /b 1

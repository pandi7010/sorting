@echo off
setlocal EnableExtensions
title LAMTON V2 - INSTALL
cd /d "%~dp0"
echo =====================================================================
echo  LAMTON V2 - install the Python environment (sorter + CNN, CPU only)
echo  Folder: %~dp0
echo  Creates .venv inside this folder. Nothing outside this folder is changed
echo  (except a Python installation, if you install one yourself).
echo  GPU later: UPGRADE_TO_GPU.bat
echo =====================================================================

REM ---------------------------------------------------------------- 1. Python 3.10 - 3.13, 64-bit
set "PY="
for %%V in (3.12 3.11 3.10 3.13) do call :try "py -%%V"
call :try "python"
if not defined PY goto :nopython
echo.
echo [1/4] Python found:
%PY% -c "import sys; print('      ', sys.version.split()[0], sys.executable)"

REM ---------------------------------------------------------------- long path check
REM pip fails inside long folder names (Windows 260 character limit, e.g. in PyQt5\Qt5\qml\...).
set "LONGOK=1"
%PY% -c "import sys; sys.exit(0 if len(sys.argv[1]) <= 90 else 1)" "%~dp0." >nul 2>&1
if errorlevel 1 (
    set "LONGOK=0"
    reg query "HKLM\SYSTEM\CurrentControlSet\Control\FileSystem" /v LongPathsEnabled 2>nul | find "0x1" >nul && set "LONGOK=1"
)
if "%LONGOK%"=="0" goto :longpath

REM ---------------------------------------------------------------- 2. virtual environment
set "VPY=%~dp0.venv\Scripts\python.exe"
if exist "%VPY%" (
    echo [2/4] .venv already exists - it is reused.
) else (
    echo [2/4] Creating .venv ...
    %PY% -m venv "%~dp0.venv"
    if errorlevel 1 goto :venvfail
)
"%VPY%" -c "import sys" >nul 2>&1 || goto :venvfail

REM ---------------------------------------------------------------- 3. required packages (internet, or offline wheels\)
set "SRC="
if exist "%~dp0wheels\*.whl" (
    set "SRC=--no-index --find-links=%~dp0wheels"
    echo [3/4] OFFLINE install from the wheels folder ...
) else (
    echo [3/4] Installing the required packages from the internet ...
)
"%VPY%" -m pip install --disable-pip-version-check %SRC% -r "%~dp0requirements.txt" -c "%~dp0constraints.txt"
if errorlevel 1 goto :pipfail
"%VPY%" -c "import PyQt5.QtWidgets, cv2, numpy, serial, pymodbus; print('Required packages: OK')"
if errorlevel 1 goto :pipfail

REM ---------------------------------------------------------------- 4. CNN packages, CPU only (about 350 MB download)
echo [4/4] Installing the CNN packages (PyTorch CPU, torchvision, ultralytics, ONNX) ...
"%VPY%" -m pip install --disable-pip-version-check %SRC% -r "%~dp0requirements_cnn.txt" -c "%~dp0constraints.txt"
if errorlevel 1 goto :cnnfail
if exist "%~dp0cnn\check_cnn.py" "%VPY%" "%~dp0cnn\check_cnn.py"

echo.
echo =====================================================================
echo  INSTALL FINISHED.  Next:  SYSTEM_CHECK.bat   then   START_SORTER.bat
echo =====================================================================
pause
exit /b 0

REM ---------------------------------------------------------------- helpers
:try
if defined PY exit /b 0
%~1 -c "import sys; sys.exit(0 if (3, 10) <= sys.version_info[:2] <= (3, 13) and sys.maxsize > 2**32 else 1)" >nul 2>&1
if errorlevel 1 exit /b 0
set "PY=%~1"
exit /b 0

:nopython
echo.
echo  ERROR: no 64-bit Python 3.10 - 3.13 found.
echo  Install Python 3.12:  drivers\python-3.12.10-amd64.exe   (or python.org)
echo    - tick "Add python.exe to PATH"
echo    - then run INSTALL.bat again.
echo  (Python 3.14 is too new for numpy 2.2.6.)
echo.
pause
exit /b 1

:longpath
echo.
echo  ERROR: this folder path is too long (%~dp0).
echo  Windows stops installing packages in long folder names (260 character limit).
echo  Copy the whole package to a SHORT folder, e.g.  C:\LAMTON_V2   and run INSTALL.bat there.
echo  (or enable "LongPathsEnabled" in Windows, then run INSTALL.bat again)
echo.
pause
exit /b 1

:venvfail
echo.
echo  ERROR: could not create the virtual environment .venv
echo  Is the folder writable? Copy the package to C:\LAMTON_V2 first (not run from the USB drive).
pause
exit /b 1

:pipfail
echo.
echo  ERROR: installing the required packages failed (see the messages above).
echo  - Internet connection OK?  Or put the offline wheels folder next to this file (MAKE_OFFLINE_WHEELS.bat).
echo  - Run INSTALL.bat again - finished packages are not downloaded twice.
pause
exit /b 1

:cnnfail
echo.
echo  WARNING: the CNN packages could not be installed. The sorter still starts and sorts
echo  (colour / HSV / contour); only CNN inference and CNN training are unavailable.
echo  Run INSTALL.bat again when the internet / wheels folder is available.
pause
exit /b 2

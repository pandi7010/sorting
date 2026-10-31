@echo off
title BUILD STANDALONE APPLICATION v2 (.EXE) - LAMTON
cd /d "%~dp0"
rem ======================================================================
rem  Reliable build: a clean virtual environment (.venv_build) with the exact
rem  versions from requirements.txt, then PyInstaller. Includes all Phase 1-4
rem  modules and config files. The old BUILD_STANDALONE_EXE.bat is unchanged.
rem  Needs ~5 GB free disk (torch is big). Output: dist\LAMTON_COLOUR_SORTER\
rem ======================================================================

where py >nul 2>nul
if %errorlevel%==0 (set "PY=py -3.12") else (set "PY=python")

if not exist ".venv_build\Scripts\python.exe" (
    echo Creating build environment .venv_build ...
    %PY% -m venv .venv_build || goto :fail
)
call ".venv_build\Scripts\activate.bat" || goto :fail
python -m pip install --upgrade pip
python -m pip install -r requirements.txt || goto :fail

echo Running offline tests before building ...
python tools\test_ejector_protocol.py || goto :fail
python tools\test_fusion_fix.py || goto :fail

python -m PyInstaller --noconfirm --onedir --windowed ^
    --name "LAMTON_COLOUR_SORTER" ^
    --icon "app_icon.ico" ^
    --add-data "app_icon.ico;." ^
    --add-data "app_icon.png;." ^
    --add-data "logo.png;." ^
    --add-data "hsv.png;." ^
    --add-data "background.png;." ^
    --add-data "bg2.png;." ^
    --add-data "recipes.json;." ^
    --add-data "camera_config.json;." ^
    --add-data "ejector_config.json;." ^
    --add-data "light_config.json;." ^
    --add-data "bg_config.json;." ^
    --add-data "system_info.json;." ^
    --add-data "system_config.json;." ^
    --add-data "wiper_config.json;." ^
    --add-data "tcm_license.dat;." ^
    --add-data "manufacturer_data_v3.db;." ^
    --add-data "features_config.json;." ^
    --add-data "line_sync_config.json;." ^
    --add-data "product_presets.json;." ^
    --add-data "best.pt;." ^
    --add-binary "MVCAMSDK_X64.dll;." ^
    --hidden-import mvsdk ^
    --hidden-import ejector_link ^
    --hidden-import features ^
    --hidden-import timing_log ^
    --hidden-import line_sync ^
    --hidden-import channel_map ^
    --hidden-import object_stream ^
    --hidden-import capture_engine ^
    --hidden-import capture_ui ^
    --hidden-import relay ^
    LAMTON_LINE_NEW_ESP32.py || goto :fail

echo.
echo ===================================================
echo   BUILD OK: dist\LAMTON_COLOUR_SORTER\LAMTON_COLOUR_SORTER.exe
echo   Settings files are inside dist\LAMTON_COLOUR_SORTER\_internal\
echo ===================================================
pause
exit /b 0

:fail
echo.
echo *** BUILD FAILED - see the messages above ***
pause
exit /b 1

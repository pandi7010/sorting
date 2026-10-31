@echo off
title BUILD STANDALONE APPLICATION (.EXE)
cd /d "%~dp0"
echo ===================================================
echo   LAMBTON COLOUR SORTER - BUILD .EXE PACKAGE
echo ===================================================
echo.
echo Installing / Verifying PyInstaller...
pip install pyinstaller

echo.
echo Compiling LAMTON_LINE_NEW_ESP32.py into Standalone Application...
pyinstaller --noconfirm --onedir --windowed ^
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
    --add-data "mvsdk.py;." ^
    --add-data "best.pt;." ^
    LAMTON_LINE_NEW_ESP32.py

echo.
echo ===================================================
echo   BUILD COMPLETED! Executable is in: dist\LAMTON_COLOUR_SORTER\
echo ===================================================
pause

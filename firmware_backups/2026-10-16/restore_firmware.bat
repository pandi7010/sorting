@echo off
REM ==========================================================================
REM  Restore the EJECTOR ESP32 to the exact firmware it had on 16 Oct 2026
REM  (full 4 MB flash image read before v2.0 was uploaded).
REM
REM  Usage:   restore_firmware.bat            (asks for the COM port)
REM           restore_firmware.bat COM13
REM
REM  Close the sorter program, testers and Serial Monitor first.
REM  If it stays at "Connecting....": press and HOLD the BOOT button on the
REM  ESP32 until the percentage starts, then release it.
REM ==========================================================================
setlocal
cd /d "%~dp0"
set IMG=ejector_COM13_original_full_4MB.bin
set ESPTOOL=..\tools\esptool_v4.6.exe

if not exist "%IMG%" ( echo ERROR: %IMG% not found in %CD% & pause & exit /b 1 )
if not exist "%ESPTOOL%" ( echo ERROR: %ESPTOOL% not found & pause & exit /b 1 )

set PORT=%1
if "%PORT%"=="" set /p PORT=Ejector ESP32 COM port (e.g. COM13):
if "%PORT%"=="" ( echo No port given. & pause & exit /b 1 )

echo.
echo Expected SHA-256 of the image (see SHA256.txt):
type SHA256.txt
echo.
echo This will ERASE the ESP32 on %PORT% and write back the original firmware.
set /p OK=Type YES to continue:
if /i not "%OK%"=="YES" ( echo Cancelled. & pause & exit /b 1 )

REM 115200 baud: 460800 hung on this board's USB link during the backup.
"%ESPTOOL%" --chip esp32 --port %PORT% --baud 115200 write_flash --flash_mode keep --flash_freq keep --flash_size keep 0x0 "%IMG%"
if errorlevel 1 ( echo. & echo RESTORE FAILED - see the messages above. & pause & exit /b 1 )

echo.
echo Restore finished (esptool verified the written data). Unplug and re-plug the ESP32.
echo The old firmware talks at 115200 baud; the sorter detects it automatically (legacy mode).
pause

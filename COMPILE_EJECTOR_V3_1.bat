@echo off
setlocal enabledelayedexpansion
REM Compile check (NO upload) of ESP32_63CH_Ejector_v3_1 (and v3_0) with the arduino-cli inside Arduino IDE 2.
REM Writes logs\compile_v3_1.txt. Made 2 Nov 2026.
cd /d "%~dp0"
if not exist logs mkdir logs
set LOG=logs\compile_v3_1.txt
echo COMPILE %DATE% %TIME% > %LOG%
set CLI=
if exist "%LOCALAPPDATA%\Programs\Arduino IDE\resources\app\lib\backend\resources\arduino-cli.exe" set CLI="%LOCALAPPDATA%\Programs\Arduino IDE\resources\app\lib\backend\resources\arduino-cli.exe"
if exist "%ProgramFiles%\Arduino IDE\resources\app\lib\backend\resources\arduino-cli.exe" set CLI="%ProgramFiles%\Arduino IDE\resources\app\lib\backend\resources\arduino-cli.exe"
if not defined CLI (
  echo arduino-cli.exe NOT FOUND inside Arduino IDE >> %LOG%
  goto end
)
echo CLI=!CLI! >> %LOG%
!CLI! version >> %LOG% 2>&1
!CLI! core list >> %LOG% 2>&1
if not exist C:\lamtmp mkdir C:\lamtmp
for %%S in (ESP32_63CH_Ejector_v3_1 ESP32_63CH_Ejector_v3_0) do (
  echo. >> %LOG%
  echo ===== %%S >> %LOG%
  !CLI! compile --fqbn esp32:esp32:esp32 --build-path C:\lamtmp\%%S "%~dp0%%S" >> %LOG% 2>&1
  echo EXITCODE %%S = !ERRORLEVEL! >> %LOG%
)
:end
echo DONE >> %LOG%
echo Finished. Results: %LOG%
timeout /t 5

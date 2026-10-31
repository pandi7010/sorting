@echo off
:: Self-elevate to Administrator if not already elevated
>nul 2>&1 "%SYSTEMROOT%\system32\cacls.exe" "%SYSTEMROOT%\system32\config\system"
if '%errorlevel%' NEQ '0' (
    echo Requesting Administrative Privileges...
    powershell -Command "Start-Process cmd -ArgumentList '/c', '"%~f0"' -Verb RunAs"
    exit /b
)

echo ========================================================
echo Copying updated Lambton Ejector files to Program Files...
echo ========================================================
set SRC=D:\lambton-main
set DST=C:\Program Files (x86)\MindVision\Demo\Python\LAMTON_V1

copy /y "%SRC%\LAMTON_LINE_NEW_ESP32.py" "%DST%\LAMTON_LINE_NEW_ESP32.py"
copy /y "%SRC%\ejector_link.py" "%DST%\ejector_link.py"
copy /y "%SRC%\port_detect.py" "%DST%\port_detect.py"
copy /y "%SRC%\camera_config.json" "%DST%\camera_config.json"
copy /y "%SRC%\bg_config.json" "%DST%\bg_config.json"

if %ERRORLEVEL% EQU 0 (
    echo.
    echo [SUCCESS] All files successfully copied to Program Files!
) else (
    echo.
    echo [ERROR] Failed to copy files.
)
pause

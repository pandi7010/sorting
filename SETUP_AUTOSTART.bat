@echo off
title Lampton V6 Auto-Start Setup
echo =======================================
echo   Setting up Auto-Start for LAMTON
echo =======================================
echo.

set "targetPath=%~dp0LAMTON_COLOUR_SORTER.exe"
set "shortcutPath=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\LamptonApp.lnk"

echo Creating shortcut for %targetPath%...
echo Set oWS = WScript.CreateObject("WScript.Shell") > CreateShortcut.vbs
echo sLinkFile = "%shortcutPath%" >> CreateShortcut.vbs
echo Set oLink = oWS.CreateShortcut(sLinkFile) >> CreateShortcut.vbs
echo oLink.TargetPath = "%targetPath%" >> CreateShortcut.vbs
echo oLink.WorkingDirectory = "%~dp0" >> CreateShortcut.vbs
echo oLink.Save >> CreateShortcut.vbs

cscript CreateShortcut.vbs
del CreateShortcut.vbs

echo.
echo Auto-start shortcut created successfully at:
echo %shortcutPath%
echo.
pause

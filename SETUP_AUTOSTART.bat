@echo off
title LAMBTON Auto-Start Setup
REM 18 Nov 2026: starts the CURRENT Python code (START_LAMTON_SORTER.bat in this folder) after Windows login.
REM Before, this made a shortcut to the old built LAMTON_COLOUR_SORTER.exe - an old copy of the program that
REM gave errors at start-up (the exe itself is NOT deleted; it is just not started any more).
echo =======================================
echo   Setting up Auto-Start for LAMBTON
echo =======================================
echo.

set "targetPath=%~dp0START_LAMTON_SORTER.bat"
set "workDir=%~dp0"
if "%workDir:~-1%"=="\" set "workDir=%workDir:~0,-1%"
set "startupDir=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
set "shortcutPath=%startupDir%\LAMTON_COLOUR_SORTER.lnk"

if not exist "%targetPath%" (
  echo ERROR: %targetPath% not found. Run this file from the sorter folder.
  pause
  exit /b 1
)

REM The old shortcut name of this script started the old exe too: rename it (not deleted) so it no longer runs.
if exist "%startupDir%\LamptonApp.lnk" (
  ren "%startupDir%\LamptonApp.lnk" "LamptonApp.lnk.old"
  echo Old shortcut LamptonApp.lnk renamed to LamptonApp.lnk.old - it will not start any more.
)

echo Creating shortcut for %targetPath%...
echo Set oWS = WScript.CreateObject("WScript.Shell") > "%TEMP%\LamtonShortcut.vbs"
echo sLinkFile = "%shortcutPath%" >> "%TEMP%\LamtonShortcut.vbs"
echo Set oLink = oWS.CreateShortcut(sLinkFile) >> "%TEMP%\LamtonShortcut.vbs"
echo oLink.TargetPath = "%targetPath%" >> "%TEMP%\LamtonShortcut.vbs"
echo oLink.WorkingDirectory = "%workDir%" >> "%TEMP%\LamtonShortcut.vbs"
echo oLink.Description = "LAMTON 63-Channel Optical Colour Sorter" >> "%TEMP%\LamtonShortcut.vbs"
echo oLink.Save >> "%TEMP%\LamtonShortcut.vbs"

cscript //nologo "%TEMP%\LamtonShortcut.vbs"
del "%TEMP%\LamtonShortcut.vbs"

echo.
echo Auto-start shortcut created:
echo   %shortcutPath%
echo   -^> %targetPath%
echo After the next Windows login the sorter starts by itself (keep its black console window open).
echo.
pause

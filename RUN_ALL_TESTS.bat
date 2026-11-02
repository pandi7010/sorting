@echo off
setlocal enabledelayedexpansion
REM Runs every tools\test_*.py with the project .venv (real PyQt5) and writes logs\test_run.txt
REM Made 2 Nov 2026. The tests use simulated hardware only.
cd /d "%~dp0"
if not exist logs mkdir logs
set LOG=logs\test_run.txt
set QT_QPA_PLATFORM=offscreen
set PY=python
if exist ".venv\Scripts\python.exe" set PY=.venv\Scripts\python.exe
copy /y ejector_config.json logs\ejector_config.before_tests.json >nul
copy /y system_config.json logs\system_config.before_tests.json >nul
echo TEST RUN %DATE% %TIME% > %LOG%
echo PY=%PY% >> %LOG%
%PY% --version >> %LOG% 2>&1
for %%F in (tools\test_*.py) do (
  echo. >> %LOG%
  echo ===== %%F >> %LOG%
  %PY% "%%F" >> %LOG% 2>&1
  echo EXITCODE %%F = !ERRORLEVEL! >> %LOG%
)
REM the sorter start-up inside some tests rewrites these two files - put them back
copy /y logs\ejector_config.before_tests.json ejector_config.json >nul
copy /y logs\system_config.before_tests.json system_config.json >nul
echo. >> %LOG%
echo DONE %DATE% %TIME% >> %LOG%
echo Finished. Results: %LOG%
timeout /t 5

@echo off
setlocal
title LAMTON V2 - UPGRADE TO GPU
REM Replaces the CPU PyTorch in .venv by the matching NVIDIA CUDA build (needs internet, GPU + driver installed).
REM   UPGRADE_TO_GPU.bat              upgrade (asks YES)
REM   UPGRADE_TO_GPU.bat --dry-run    only show the plan        UPGRADE_TO_GPU.bat --verify   check the GPU is used
REM   UPGRADE_TO_GPU.bat --back-to-cpu
cd /d "%~dp0"
set "PY=%~dp0.venv\Scripts\python.exe"
if not exist "%PY%" (
    echo Run INSTALL.bat first.
    pause
    exit /b 1
)
echo Close the sorter and any CNN training first.
"%PY%" UPGRADE_TO_GPU.py %*
echo.
pause

@echo off
setlocal
title LAMTON CNN training (CPU)
REM Trains a MobileNetV2 CNN on class folders (ImageFolder: <dataset>\<class>\*.jpg). Runs on the CPU.
REM   RUN_CNN_TRAIN.bat --dataset D:\data\dhall --out %~dp0models --epochs 10
REM   RUN_CNN_TRAIN.bat --dataset ... --out ... --classes GOOD,BAD
REM The ImageNet starting weights are in torch_home\ (no internet needed).
cd /d "%~dp0"
set "TORCH_HOME=%~dp0torch_home"
set "PY=%~dp0..\.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"
"%PY%" training.py %*
pause

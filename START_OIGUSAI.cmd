@echo off
chcp 65001 >nul
title ÕigusAI V8
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0START_OIGUSAI.ps1"
if errorlevel 1 (
  echo.
  echo Käivitamine ebaõnnestus. Veateade on ülal.
  pause
)

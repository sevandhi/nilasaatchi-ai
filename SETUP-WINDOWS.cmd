@echo off
rem One-time setup on Windows: double-click this file (Docker Desktop must be running).
cd /d "%~dp0"
rem files downloaded from the internet are blocked by Windows; unblock the scripts first
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-ChildItem -Path '%~dp0scripts' -Filter *.ps1 | Unblock-File"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\demo_setup.ps1"
pause

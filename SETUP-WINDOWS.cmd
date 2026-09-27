@echo off
rem One-time setup on Windows: double-click this file (Docker Desktop must be running).
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\demo_setup.ps1"
pause

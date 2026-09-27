@echo off
rem Start NilaSaatchi AI on Windows: double-click this file (Docker Desktop must be running).
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\demo_run.ps1"

@echo off
powershell -ExecutionPolicy Bypass -NoProfile -File "%~dp0manage.ps1" start
if %ERRORLEVEL% NEQ 0 pause

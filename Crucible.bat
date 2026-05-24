@echo off
title Crucible

echo.
echo  ===  Crucible  ===
echo.
echo   1)  Setup    -  First-time install ^(creates venv, installs deps, builds frontend^)
echo   2)  Start    -  Launch the app at http://localhost:8000
echo   3)  Update   -  Pull latest changes and rebuild
echo.
set /p choice=  Enter choice [1-3]:

if "%choice%"=="1" goto setup
if "%choice%"=="2" goto start
if "%choice%"=="3" goto update
echo.
echo  Invalid choice.
pause
exit /b 1

:setup
powershell -ExecutionPolicy Bypass -NoProfile -File "%~dp0manage.ps1" setup
pause
exit /b

:start
powershell -ExecutionPolicy Bypass -NoProfile -File "%~dp0manage.ps1" start
pause
exit /b

:update
powershell -ExecutionPolicy Bypass -NoProfile -File "%~dp0manage.ps1" update
pause
exit /b

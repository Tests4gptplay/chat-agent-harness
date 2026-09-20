@echo off
setlocal EnableExtensions
title CAH One-Click Start
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0Start_CAH.ps1" %*
set "CAH_EXIT_CODE=%ERRORLEVEL%"
if /I "%~1"=="-NonInteractive" exit /b %CAH_EXIT_CODE%
echo.
echo Press any key to close this window...
pause >nul
exit /b %CAH_EXIT_CODE%

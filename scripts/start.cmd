@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1" -DockerContext desktop-linux %*
exit /b %errorlevel%

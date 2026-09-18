@echo off
rem Doble clic para iniciar el control remoto del TV (usa start-tv-remote.ps1)
rem Para acceso desde el movil: start-tv-remote.bat -Lan
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-tv-remote.ps1" %*
pause

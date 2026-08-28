@echo off
rem Stop: panel + collection Edge only (daily Edge untouched)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0启动工作台.ps1" -Stop
pause
@echo off
cd /d "%~dp0"
echo Type these into the phone app (PC connection):
echo.
python server.py --info
echo.
pause

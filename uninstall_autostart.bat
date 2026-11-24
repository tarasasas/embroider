@echo off
set "VBS=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\EmbroideryPhoneServer.vbs"
if exist "%VBS%" del "%VBS%"
rem Stop any running hidden server
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='pythonw.exe'\" | Where-Object { $_.CommandLine -like '*server.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
echo Removed. The phone server will no longer start with Windows.
pause

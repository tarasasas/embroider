@echo off
rem Stops every running copy of the phone server (hidden or in a window).
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { ($_.Name -eq 'python.exe' -or $_.Name -eq 'pythonw.exe') -and $_.CommandLine -like '*server.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force; 'Stopped ' + $_.ProcessId }"
echo Done. Start it again with run_server.bat (or sign out and back in if you use auto-start).
pause

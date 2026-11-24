@echo off
rem Starts the phone server (hidden) every time you sign in to Windows, so the phone can always reach it.
cd /d "%~dp0"
set "HERE=%~dp0"
set "STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
set "VBS=%STARTUP%\EmbroideryPhoneServer.vbs"

for /f "delims=" %%P in ('where pythonw 2^>nul') do set "PYW=%%P" & goto found
echo Could not find pythonw. Is Python installed and on PATH?
pause
exit /b 1
:found

python -m pip install -q -r requirements.txt

> "%VBS%" echo Set sh = CreateObject("WScript.Shell")
>> "%VBS%" echo sh.CurrentDirectory = "%HERE%"
>> "%VBS%" echo sh.Run """%PYW%"" ""%HERE%server.py""", 0, False

echo Installed. The phone server will start hidden each time you sign in to Windows.
echo Starting it now...
wscript "%VBS%"
echo.
echo Your phone connection details:
python server.py --info
echo.
echo If Windows asks, allow Python through the firewall on PRIVATE networks.
pause

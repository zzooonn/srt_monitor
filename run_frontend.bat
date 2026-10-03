@echo off
setlocal
cd /d "%~dp0"
if errorlevel 1 exit /b 1

echo Opening Rail Monitor: http://127.0.0.1:8000/
echo The server must be running. Refresh if it is still starting.
start "" "http://127.0.0.1:8000/"
exit /b %ERRORLEVEL%

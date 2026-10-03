@echo off
setlocal
cd /d "%~dp0"
if errorlevel 1 exit /b 1

call "%~dp0run_backend.bat" --check
if errorlevel 1 exit /b 1
if /i "%~1"=="--check" exit /b 0
call "%~dp0run_frontend.bat"
if errorlevel 1 exit /b 1
call "%~dp0run_backend.bat"
exit /b %ERRORLEVEL%

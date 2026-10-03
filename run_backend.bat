@echo off
setlocal
cd /d "%~dp0"
if errorlevel 1 exit /b 1

set "PYTHON_CMD="
if not exist ".venv\Scripts\python.exe" goto try_py
"%~dp0.venv\Scripts\python.exe" -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1
if errorlevel 1 goto try_py
set PYTHON_CMD="%~dp0.venv\Scripts\python.exe"
goto python_ready

:try_py
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1
if errorlevel 1 goto try_python
set "PYTHON_CMD=py -3"
goto python_ready

:try_python
python -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1
if errorlevel 1 goto missing_python
set "PYTHON_CMD=python"

:python_ready
echo Using Python: %PYTHON_CMD%
%PYTHON_CMD% -c "import fastapi, uvicorn, pydantic, requests, SRT, korail2" >nul 2>&1
if errorlevel 1 goto missing_dependencies
if /i "%~1"=="--check" exit /b 0
echo Rail Monitor: http://127.0.0.1:8000/
echo Press Ctrl+C to stop the server.
%PYTHON_CMD% -m uvicorn backend.app:app --host 127.0.0.1 --port 8000
set "SERVER_EXIT=%ERRORLEVEL%"
if not "%SERVER_EXIT%"=="0" echo [ERROR] Server failed. Check the error above and whether port 8000 is already in use.
exit /b %SERVER_EXIT%

:missing_python
echo [ERROR] No working Python 3.11 or newer was found.
echo Install Python: https://www.python.org/downloads/
echo Enable PATH or the Python launcher, then run install_web.bat.
exit /b 1

:missing_dependencies
echo [ERROR] Web dependencies are missing or cannot be imported.
echo Run install_web.bat first. No packages were installed by this runner.
exit /b 1

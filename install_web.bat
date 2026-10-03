@echo off
setlocal
cd /d "%~dp0"
if errorlevel 1 exit /b 1

set "PYTHON_CMD="
set "USING_VENV="
if not exist ".venv\Scripts\python.exe" goto try_py
"%~dp0.venv\Scripts\python.exe" -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1
if errorlevel 1 goto try_py
set PYTHON_CMD="%~dp0.venv\Scripts\python.exe"
set "USING_VENV=1"
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
git --version >nul 2>&1
if errorlevel 1 goto missing_git
if /i "%~1"=="--check" exit /b 0
if defined USING_VENV goto install_dependencies
if exist ".venv" goto invalid_venv
echo Creating the project virtual environment...
%PYTHON_CMD% -m venv "%~dp0.venv"
if errorlevel 1 goto failed_venv
"%~dp0.venv\Scripts\python.exe" -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1
if errorlevel 1 goto failed_venv
set PYTHON_CMD="%~dp0.venv\Scripts\python.exe"

:install_dependencies
set "KORAIL_REQUIREMENT="
for /f "usebackq tokens=1,* delims=@" %%A in ("%~dp0requirements-web.txt") do if "%%A"=="korail2 " set "KORAIL_REQUIREMENT=%%A@%%B"
if not defined KORAIL_REQUIREMENT goto missing_korail_requirement
echo Installing Rail Monitor dependencies into .venv...
%PYTHON_CMD% -m pip install -r "%~dp0requirements-web.txt"
if errorlevel 1 goto failed_install
rem An installed PyPI package may have the same version as the pinned fork.
rem Reinstall only the fork; keep the pin in requirements-web.txt as one source.
%PYTHON_CMD% -m pip install --force-reinstall --no-deps "%KORAIL_REQUIREMENT%"
if errorlevel 1 goto failed_install
echo Installation complete. Run run_web.bat.
exit /b 0

:missing_python
echo [ERROR] No working Python 3.11 or newer was found.
echo Install Python: https://www.python.org/downloads/
echo Enable PATH or the Python launcher, then retry.
exit /b 1

:missing_git
echo [ERROR] Git is required to install the pinned korail2 dependency.
echo Install Git: https://git-scm.com/downloads/win
echo Open a new terminal after installing Git, then retry.
exit /b 1

:invalid_venv
echo [ERROR] The existing .venv is unusable or uses Python older than 3.11.
echo Repair it or remove only this project's .venv and run this installer again.
exit /b 1

:failed_venv
echo [ERROR] Could not create a working Python 3.11+ virtual environment.
echo Check the Python installation and write access to the project folder.
exit /b 1

:failed_install
echo [ERROR] Dependency installation failed. See the error above.
echo Check internet access and Git access to the pinned korail2 repository.
exit /b 1

:missing_korail_requirement
echo [ERROR] The pinned korail2 requirement is missing from requirements-web.txt.
echo Restore the project's dependency file, then retry.
exit /b 1

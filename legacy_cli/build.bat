@echo off
cd /d "%~dp0"
echo Building srt_monitor.exe ...

pip show pyinstaller > nul 2>&1
if errorlevel 1 (
    echo Installing PyInstaller...
    pip install pyinstaller
)

if exist dist\srt_monitor.exe del /f dist\srt_monitor.exe

pyinstaller --onefile --name srt_monitor --console --clean --paths .. srt_monitor.py

echo.
if exist dist\srt_monitor.exe (
    echo Build SUCCESS! Send dist\srt_monitor.exe to your friends.
) else (
    echo Build FAILED. Check the error messages above.
)

pause

@echo off
setlocal
where uv.exe >nul 2>nul
if errorlevel 1 (
    if exist "%USERPROFILE%\.local\bin\uv.exe" set "PATH=%USERPROFILE%\.local\bin;%PATH%"
)
echo UCPC source setup
echo Installing dependencies and checking the local configuration...
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1"
set "ucpc_setup_result=%ERRORLEVEL%"
if "%ucpc_setup_result%"=="0" (
    echo.
    echo Setup complete. Double-click start.vbs to launch UCPC.
) else (
    echo.
    echo Setup failed. Read START_HERE.txt or SOURCE_START_HERE.txt.
    echo If uv was not found, install uv and run SETUP.cmd again.
)
if not "%UCPC_SETUP_NO_PAUSE%"=="1" pause
exit /b %ucpc_setup_result%

@echo off
setlocal
chcp 65001 >nul
if not exist "%~dp0runtime\python.exe" (
    echo Run Uninstall.cmd from the extracted Windows release ZIP.
    pause
    exit /b 1
)
"%~dp0runtime\python.exe" -X utf8 -B "%~dp0install.py" uninstall
set "result=%errorlevel%"
pause
exit /b %result%

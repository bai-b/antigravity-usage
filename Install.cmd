@echo off
setlocal
chcp 65001 >nul
if not exist "%~dp0runtime\python.exe" (
    echo Download the Windows ZIP asset from GitHub Releases, then extract the whole ZIP.
    pause
    exit /b 1
)
"%~dp0runtime\python.exe" -X utf8 -B "%~dp0install.py" install
set "result=%errorlevel%"
pause
exit /b %result%

@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set "PY="
if exist "%~dp0runtime\python.exe" set "PY=%~dp0runtime\python.exe"
if not defined PY if exist "%USERPROFILE%\.gemini\config\plugins\agy-usage\runtime\python.exe" set "PY=%USERPROFILE%\.gemini\config\plugins\agy-usage\runtime\python.exe"
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python313\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
if not defined PY where python >nul 2>nul && set "PY=python"
if not defined PY (
  echo 未找到 Python 解释器：请安装 Python 3.10+ 或先安装本插件的运行时。
  pause
  exit /b 1
)
set "PYTHONIOENCODING=utf-8"
"%PY%" "%~dp0release.py" %*
set "RC=%ERRORLEVEL%"
if not %RC%==0 pause
exit /b %RC%

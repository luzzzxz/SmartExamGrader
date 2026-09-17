@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion
set "BASE_DIR=%~dp0"
set "PYTHON_CMD="
set "PYTHON_EXE=%LOCALAPPDATA%\Programs\Python\Python314\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=C:\Program Files\Python314\python.exe"
if exist "%PYTHON_EXE%" set PYTHON_CMD="%PYTHON_EXE%"
if not defined PYTHON_CMD (
  py -3.14 --version >nul 2>nul
  if not errorlevel 1 set "PYTHON_CMD=py -3.14"
)
if not defined PYTHON_CMD (
  python --version >nul 2>nul
  if not errorlevel 1 set "PYTHON_CMD=python"
)
if not defined PYTHON_CMD (
  echo 错误：未检测到 Python 环境。
  echo 请确认已安装 Python 3.14，或在系统环境变量 PATH 中配置 python。
  pause
  exit /b 1
)
set "PYTHONPATH=%BASE_DIR%runtime\pydeps314;%BASE_DIR%app;%PYTHONPATH%"
%PYTHON_CMD% "%BASE_DIR%app\migrate_from_old_version.py" %*
set "EXIT_CODE=%ERRORLEVEL%"
if not "!EXIT_CODE!"=="0" (
  echo.
  echo 迁移工具退出，返回代码 !EXIT_CODE!。
  pause
)
exit /b !EXIT_CODE!

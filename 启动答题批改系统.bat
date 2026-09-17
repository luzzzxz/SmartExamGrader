@echo off
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
  echo ERROR: Python 3.14 not found.
  echo Please install Python 3.14, then run this launcher again.
  pause
  exit /b 1
)
set "PYTHONPATH=%BASE_DIR%runtime\pydeps314;%PYTHONPATH%"
%PYTHON_CMD% "%BASE_DIR%answer_card_stable_bootstrap.py"
set "EXIT_CODE=%ERRORLEVEL%"
if not "!EXIT_CODE!"=="0" (
  echo.
  echo ERROR: app exited with code !EXIT_CODE!.
  pause
)
exit /b !EXIT_CODE!

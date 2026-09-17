@echo off
setlocal
cd /d "%~dp0"

where py >nul 2>nul
if errorlevel 1 (
  echo ERROR: Windows Python launcher ^(py.exe^) not found.
  echo Please install 64-bit Python 3.13 with the py launcher enabled.
  pause
  exit /b 1
)

set "PY313_CHECK=%TEMP%\answer_card_python313_%RANDOM%_%RANDOM%.ok"
if exist "%PY313_CHECK%" del /q "%PY313_CHECK%" >nul 2>nul
py -3.13 -c "from pathlib import Path; Path(r'%PY313_CHECK%').write_text('ok', encoding='ascii')" >nul 2>nul
if not exist "%PY313_CHECK%" (
  echo Python 3.13 is not installed. Trying to install it now...
  py install 3.13
  if exist "%PY313_CHECK%" del /q "%PY313_CHECK%" >nul 2>nul
  py -3.13 -c "from pathlib import Path; Path(r'%PY313_CHECK%').write_text('ok', encoding='ascii')" >nul 2>nul
  if not exist "%PY313_CHECK%" (
    echo.
    echo ERROR: Python 3.13 could not be installed automatically.
    echo Run this command in Command Prompt: py install 3.13
    echo Then run this installer again.
    pause
    exit /b 1
  )
)
del /q "%PY313_CHECK%" >nul 2>nul

if not exist "app\requirements-paddleocr.txt" (
  echo ERROR: app\requirements-paddleocr.txt not found.
  pause
  exit /b 1
)

if not exist "app\paddleocr_env\Scripts\python.exe" (
  echo Creating PaddleOCR environment...
  py -3.13 -m venv "app\paddleocr_env"
  if errorlevel 1 goto :failed
  if not exist "app\paddleocr_env\Scripts\python.exe" goto :failed
)

if not exist "app\paddleocr_env\Scripts\python.exe" goto :failed

echo Installing PaddleOCR dependencies. This can take several minutes...
"app\paddleocr_env\Scripts\python.exe" -m pip install --upgrade pip setuptools wheel
if errorlevel 1 goto :failed
"app\paddleocr_env\Scripts\python.exe" -m pip install -r "app\requirements-paddleocr.txt"
if errorlevel 1 goto :failed
"app\paddleocr_env\Scripts\python.exe" -m pip check
if errorlevel 1 goto :failed
"app\paddleocr_env\Scripts\python.exe" -c "import paddle, paddleocr, paddlex; print('PaddleOCR environment is ready.')"
if errorlevel 1 goto :failed

echo.
echo PaddleOCR installation completed.
echo OCR models will be downloaded on the first OCR run if they are not cached.
pause
exit /b 0

:failed
echo.
echo ERROR: PaddleOCR installation failed.
echo Check the network connection and confirm that 64-bit Python 3.13 is installed.
pause
exit /b 1

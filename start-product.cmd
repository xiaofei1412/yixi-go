@echo off
setlocal DisableDelayedExpansion
cd /d "%~dp0" || goto failed
rem A copied venv may contain python.exe but still refer to the old PC.
".venv\Scripts\python.exe" -c "import sys; assert sys.version_info >= (3,10); assert sys.prefix != sys.base_prefix" >nul 2>nul
if not errorlevel 1 goto dependencies

py -3 -c "import sys, venv; assert sys.version_info >= (3,10)" >nul 2>nul
if not errorlevel 1 (
  set "PRODUCT_PYTHON=py -3"
  goto prepare_environment
)
python -c "import sys, venv; assert sys.version_info >= (3,10)" >nul 2>nul
if not errorlevel 1 (
  set "PRODUCT_PYTHON=python"
  goto prepare_environment
)
echo No usable Python 3.10 or newer was found.
echo Install Python on THIS computer, enable Add Python to PATH, then try again.
goto failed

:prepare_environment
if not exist ".venv" goto create_environment
rem Resolve and check the exact workspace path before renaming; never delete it.
for %%I in (".venv") do set "PRODUCT_ENV_DIR=%%~fI"
for %%I in ("%~dp0.venv") do if /I not "%PRODUCT_ENV_DIR%" == "%%~fI" goto failed
:backup_name
set "PRODUCT_ENV_BACKUP=.venv.unusable-%RANDOM%-%RANDOM%"
if exist "%PRODUCT_ENV_BACKUP%" goto backup_name
ren "%PRODUCT_ENV_DIR%" "%PRODUCT_ENV_BACKUP%"
if errorlevel 1 goto failed
echo Kept the old environment in %PRODUCT_ENV_BACKUP%.

:create_environment
echo Creating a Python environment for this computer...
%PRODUCT_PYTHON% -m venv ".venv"
if errorlevel 1 goto failed

:dependencies
".venv\Scripts\python.exe" -c "import fastapi, uvicorn, sgfmill, httpx" >nul 2>nul
if errorlevel 1 (
  echo Installing product dependencies. Internet access is needed for the first setup.
  ".venv\Scripts\python.exe" -m pip install -r requirements-product.txt
  if errorlevel 1 goto failed
)
echo Open http://127.0.0.1:8000 in your browser. Keep this window open.
".venv\Scripts\python.exe" -m uvicorn server:app --host 127.0.0.1 --port 8000
if errorlevel 1 goto failed
exit /b 0
:failed
echo Could not start. Check Python, dependencies, folder permissions, or port 8000.
pause
exit /b 1


@echo off
cd /d "%~dp0"
set "VENV_PY=C:\Users\a3564\.workbuddy\binaries\python\envs\pyside6\Scripts\python.exe"
if not exist "%VENV_PY%" (
    echo [ERROR] PySide6 venv not found:
    echo   %VENV_PY%
    echo Please install it first:
    echo   ...\python\envs\pyside6\Scripts\pip install PySide6
    echo Or update VENV_PY in this script to your PySide6 python path.
    pause
    exit /b 1
)
"%VENV_PY%" "%~dp0screen_recorder_pyside6.py"

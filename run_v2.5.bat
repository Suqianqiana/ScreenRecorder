@echo off
cd /d "%~dp0"
set "VENV_PY=C:\Users\a3564\.workbuddy\binaries\python\envs\pyside6\Scripts\python.exe"
if not exist "%VENV_PY%" (
    echo [ERROR] PySide6 venv not found:
    echo   %VENV_PY%
    pause
    exit /b 1
)
echo == Running ScreenRecorder v2.5 (Acrylic/Frosted Glass Modern UI) ==
"%VENV_PY%" "%~dp0screen_recorder_pyside6_v2.5.py"
echo.
echo == Exited with code %errorlevel% ==
pause
@echo off
cd /d "%~dp0"
set "VENV_PY=C:\Users\a3564\.workbuddy\binaries\python\envs\pyside6\Scripts\python.exe"
set "PYINSTALLER=C:\Users\a3564\.workbuddy\binaries\python\envs\pyside6\Scripts\pyinstaller.exe"
if not exist "%VENV_PY%" (
    echo [ERROR] PySide6 venv not found:
    echo   %VENV_PY%
    echo Please create it first:
    echo   ...\python\versions\3.13.12\python.exe -m venv ...\python\envs\pyside6
    echo   ...\python\envs\pyside6\Scripts\pip install PySide6 pyinstaller
    pause
    exit /b 1
)
if not exist "ffmpeg.exe" (
    echo [ERROR] ffmpeg.exe not found in this folder.
    pause
    exit /b 1
)
echo == Cleaning old build artifacts ==
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
echo == Building single-file exe (PySide6) ==
"%PYINSTALLER%" --noconfirm --onefile --windowed --name ScreenRecorder --icon "assets\icon.ico" --add-binary "ffmpeg.exe;." --add-data "assets\icon.png;assets" --collect-all PySide6 screen_recorder_pyside6_v2.py
if not exist "dist\ScreenRecorder.exe" (
    echo [ERROR] Build failed, dist\ScreenRecorder.exe not produced.
    pause
    exit /b 1
)
echo == Backing up old exe ==
if exist "ScreenRecorder_app\ScreenRecorder.exe" (
    copy /y "ScreenRecorder_app\ScreenRecorder.exe" "ScreenRecorder_app\ScreenRecorder.exe.bak" >nul
)
echo == Delivering new exe to ScreenRecorder_app ==
copy /y "dist\ScreenRecorder.exe" "ScreenRecorder_app\ScreenRecorder.exe" >nul
echo == Done. Cleaning build cache ==
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
echo Build complete: ScreenRecorder_app\ScreenRecorder.exe
pause

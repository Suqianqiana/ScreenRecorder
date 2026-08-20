@echo off
cd /d "%~dp0"
if exist "ScreenRecorder_app\ScreenRecorder.exe" (
    start "" "ScreenRecorder_app\ScreenRecorder.exe"
) else (
    echo ScreenRecorder_app\ScreenRecorder.exe not found.
    echo Please run build_exe.bat first.
    pause
)

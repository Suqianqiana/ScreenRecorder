@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

REM Use WorkBuddy managed Python so this works without python on PATH.
set "PYTHON=C:\Users\a3564\.workbuddy\binaries\python\versions\3.13.12\python.exe"

if not exist "%PYTHON%" (
    echo ERROR: Managed Python not found at
    echo %PYTHON%
    echo Please install Python 3.13 via WorkBuddy or update this path.
    pause
    exit /b 1
)

if not exist "screen_recorder.py" (
    echo ERROR: screen_recorder.py not found in %~dp0
    pause
    exit /b 1
)

if not exist "ffmpeg.exe" (
    echo ERROR: ffmpeg.exe not found in %~dp0
    echo Please put ffmpeg.exe here so it can be bundled into the exe.
    pause
    exit /b 1
)

echo Checking PyInstaller...
"%PYTHON%" -c "import PyInstaller" 2>nul
if errorlevel 1 (
    echo Installing PyInstaller...
    "%PYTHON%" -m pip install --quiet pyinstaller
    if errorlevel 1 (
        echo Failed to install pyinstaller.
        pause
        exit /b 1
    )
)

echo Cleaning old build artifacts...
if exist build rd /s /q build
if exist dist  rd /s /q dist

echo Building single-file self-contained exe...
"%PYTHON%" -m PyInstaller --noconfirm --onefile --windowed --name ScreenRecorder --add-binary "ffmpeg.exe;." screen_recorder.py
if errorlevel 1 (
    echo Build failed. See error above.
    pause
    exit /b 1
)

if not exist "dist\ScreenRecorder.exe" (
    echo ERROR: dist\ScreenRecorder.exe not found after build.
    pause
    exit /b 1
)

if not exist "ScreenRecorder_app" mkdir "ScreenRecorder_app"

if exist "ScreenRecorder_app\ScreenRecorder.exe" (
    echo Backing up previous exe...
    move /Y "ScreenRecorder_app\ScreenRecorder.exe" "ScreenRecorder_app\ScreenRecorder.exe.bak" >nul
)

echo Copying new exe to ScreenRecorder_app...
copy /Y "dist\ScreenRecorder.exe" "ScreenRecorder_app\ScreenRecorder.exe" >nul

echo.
echo Build OK: ScreenRecorder_app\ScreenRecorder.exe
echo Size:
for %%F in ("ScreenRecorder_app\ScreenRecorder.exe") do echo  %%~zF bytes
pause

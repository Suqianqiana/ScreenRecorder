@echo off
cd /d "%~dp0"
set PROTO_DIAG=1
set LOGFILE=%TEMP%\proto_resize_log.txt
if exist "%LOGFILE%" del "%LOGFILE%"
set PY=C:\Users\a3564\.workbuddy\binaries\python\envs\pyside6\Scripts\python.exe
if not exist "%PY%" set PY=python
"%PY%" screen_recorder_ui_proto.py
echo.
echo ---------------------------------------------
echo Layout log saved to:
echo %LOGFILE%
echo ---------------------------------------------
pause

@echo off
cd /d "%~dp0"
set PY=C:\Users\a3564\.workbuddy\binaries\python\envs\pyside6\Scripts\python.exe
if not exist "%PY%" set PY=python
"%PY%" screen_recorder_ui_proto.py
pause

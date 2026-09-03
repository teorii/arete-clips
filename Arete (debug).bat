@echo off
rem Same app, but keeps a console so you can watch capture and upload output.
cd /d "%~dp0"
".venv\Scripts\python.exe" desktop.py
pause

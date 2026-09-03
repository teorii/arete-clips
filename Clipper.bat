@echo off
rem Launch Clipper with no console window. Logs go to clipper.log.
cd /d "%~dp0"
start "" ".venv\Scripts\pythonw.exe" desktop.py

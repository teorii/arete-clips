@echo off
rem Launch Arete with no console window. Logs go to arete.log.
rem Arete.exe is the branded launcher built by tools\make_launcher.py; it is what
rem puts the name and icon in Task Manager. Fall back to pythonw so a fresh
rem clone still starts before that build step has been run.
cd /d "%~dp0"
if exist ".venv\Scripts\Arete.exe" (
  start "" ".venv\Scripts\Arete.exe" desktop.py
) else (
  start "" ".venv\Scripts\pythonw.exe" desktop.py
)

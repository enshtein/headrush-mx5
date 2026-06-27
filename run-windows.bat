@echo off
setlocal

set "ROOT_DIR=%~dp0"
cd /d "%ROOT_DIR%"

if not exist ".venv\Scripts\python.exe" (
  py -3 -m venv .venv
)

".venv\Scripts\python.exe" -m pip install -r requirements.txt
set "PYTHONPATH=%ROOT_DIR%src"
".venv\Scripts\python.exe" -m headrush_mx5

endlocal

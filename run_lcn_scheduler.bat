@echo off
cd /d "%~dp0"
set LCN_CONTINUOUS=true
".venv\Scripts\python.exe" -u lcn_scheduler.py >> scheduler.log 2>&1

@echo off
REM Wrapper for the Windows scheduled task. Runs the crypto bot in dry-run
REM mode from its own folder and appends all output to agent.log.
cd /d "C:\Users\RyanMorgan\OneDrive - Curve Workplaces Ltd\Documents\Trading Bot"
echo ==================== %DATE% %TIME% ====================>> agent.log
"venv\Scripts\python.exe" main.py >> agent.log 2>&1

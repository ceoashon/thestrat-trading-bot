@echo off
rem Starts the execution bot and restarts it if it ever stops. Point a Task Scheduler "At startup" task at this file.
cd /d "%~dp0"
:loop
python run_bot.py --config config.toml
echo The bot stopped. Restarting in 10 seconds...
timeout /t 10 /nobreak >nul
goto loop

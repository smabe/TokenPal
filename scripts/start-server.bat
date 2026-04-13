@echo off
cd /d %~dp0..
set OLLAMA_KEEP_ALIVE=24h
start /B "" "%LOCALAPPDATA%\Programs\Ollama\ollama.exe" serve
timeout /t 3 /nobreak >nul
call .venv\Scripts\activate.bat
tokenpal-server --host 0.0.0.0
pause

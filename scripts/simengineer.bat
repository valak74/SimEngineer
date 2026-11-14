@echo off
rem Lance SimEngineer et attend iRacing (ne consomme presque rien tant que le sim est fermé).
cd /d "%~dp0\.."
".venv\Scripts\simengineer.exe" run --source live

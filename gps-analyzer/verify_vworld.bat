@echo off
chcp 65001 >nul
cd /d "%~dp0"
.venv\Scripts\python.exe -m gps_analyzer.vworld --config config.json --output output\vworld_연동검증.json %*
exit /b %errorlevel%

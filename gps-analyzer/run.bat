@echo off
chcp 65001 >nul
cd /d "%~dp0"
if "%~1"=="" (
 echo 사용법: run.bat "D:\GPS원본"
 exit /b 2
)
.venv\Scripts\python.exe -m gps_analyzer --input "%~1" --output output --work work_data --config config.json
exit /b %errorlevel%

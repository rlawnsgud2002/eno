@echo off
chcp 65001 >nul
cd /d "%~dp0"
py -3 -m venv .venv
if errorlevel 1 exit /b 1
.venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 exit /b 1
echo 설치 완료. run.bat "원본 폴더 경로" 로 실행하세요.

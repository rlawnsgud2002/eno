@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 이노베이스 GPS 프로그램
if not exist ".venv\Scripts\python.exe" (
 echo 처음 실행입니다. Python 설치 환경을 준비합니다.
 call install.bat
 if errorlevel 1 (
  echo 설치 실패. Python 3.12를 설치하고 다시 실행하세요.
  pause
  exit /b 1
 )
)
set "GPS_INPUT_DIR=%~1"
if not defined GPS_INPUT_DIR (
 echo GPS 원본 Excel이 들어 있는 폴더의 전체 경로를 입력하세요.
 echo 예: D:\GPS원본
 set /p "GPS_INPUT_DIR=원본 폴더: "
)
if not defined GPS_INPUT_DIR (
 echo 원본 폴더를 지정하지 않았습니다.
 pause
 exit /b 2
)
set "GPS_INPUT_DIR=%GPS_INPUT_DIR:"=%"
if not exist "%GPS_INPUT_DIR%\" (
 echo 폴더를 찾을 수 없습니다. 입력한 경로를 확인하세요.
 pause
 exit /b 2
)
.venv\Scripts\python.exe -m gps_analyzer --input "%GPS_INPUT_DIR%" --output output --work work_data --config config.json
if errorlevel 1 (
 echo 분석 실패. work_data\analysis.log 파일을 확인하세요.
 pause
 exit /b 1
)
echo 분석 완료. output 폴더에서 Excel 파일을 확인하세요.
start "" "%~dp0output"
pause

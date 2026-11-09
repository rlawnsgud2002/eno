# 이노베이스 프로젝트 구분

이 저장소의 GPS 분석 프로그램과 이노 관제·배차 프로그램은 **서로 다른 프로젝트**입니다.

| 구분 | 위치 | 내용 |
|---|---|---|
| GPS 체류 장소 분석 | [`gps-analyzer/`](gps-analyzer/) | GPS Excel 정제, 3분 이상 체류 후보, 공공 건물정보·카카오 연계, Excel 통합 보고서 |
| GPS 프로그램 다운로드 | [`downloads/gps/`](downloads/gps/) | Python 소스·설치 안내를 포함한 배포 ZIP |
| 이노 관제·배차 프로그램 | 별도 프로젝트로 관리 | GPS 소스와 합치거나 수정하지 않음 |

## GPS 프로그램 다운로드

**[PowerShell 직접 설치·실행 안내를 포함한 ZIP 다운로드](https://github.com/rlawnsgud2002/eno/raw/refs/heads/main/downloads/gps/이노베이스_GPS_프로그램_PowerShell안내.zip)**

- [PowerShell 설치·실행 순서](gps-analyzer/POWERSHELL_실행순서.md): Python 3.12 64비트, 의존성, API 키, 샘플·전체 분석, Excel 확인 명령
- [전체 README](gps-analyzer/README.md): 분석 기준·GIS/API·대용량 처리·검증 설명
- [기존 이노베이스_GPS_프로그램.zip](https://github.com/rlawnsgud2002/eno/raw/refs/heads/main/downloads/gps/이노베이스_GPS_프로그램.zip): 원본 ZIP을 변경하지 않고 유지

ZIP 전체를 압축 해제하고 안내를 따라 열린 PowerShell 창에 명령을 입력하세요. 가상환경의 Python을 직접 호출하며 보호 기능·실행 정책을 변경하거나 활성화 스크립트를 실행하지 않습니다. 독립 실행 EXE로 패키징하지 않았습니다. Python 또는 의존성 DLL/PYD 자체가 Windows에서 차단되는 경우에는 공식 배포본과 차단 파일명을 확인해야 하며, 스마트 앱 컨트롤 환경에서의 실행을 보장하지는 않습니다.

GPS 실행 소스·기존 배치 파일·63개 자동 테스트는 두 ZIP에서 같습니다. 이번 변경에서 실제 샘플을 포함한63개 테스트가 다시 통과했습니다. Windows 실기·실제 API 인증·전체2GB 처리는 별도 검증 대상입니다.

API 키, 실제 `.env`, GPS 원본·분석 결과·캐시는 저장소와 ZIP에 포함하지 않습니다.

# 이노베이스 프로젝트 구분

이 저장소의 GPS 분석 프로그램과 이노 관제·배차 프로그램은 **서로 다른 프로젝트**입니다.

| 구분 | 위치 | 내용 |
|---|---|---|
| GPS 체류 장소 분석 | [`gps-analyzer/`](gps-analyzer/) | GPS Excel 정제, 3분 이상 체류 후보, 공공 건물정보·카카오 연계, Excel 통합 보고서 |
| GPS 프로그램 다운로드 | [`downloads/gps/`](downloads/gps/) | 설치·실행 파일을 포함한 배포 ZIP |
| 이노 관제·배차 프로그램 | 별도 프로젝트로 관리 | 이번 GPS 소스와 합치거나 수정하지 않음 |

## GPS 프로그램 다운로드

**[이노베이스_GPS_프로그램.zip 다운로드](https://github.com/rlawnsgud2002/eno/raw/refs/heads/main/downloads/gps/%EC%9D%B4%EB%85%B8%EB%B2%A0%EC%9D%B4%EC%8A%A4_GPS_%ED%94%84%EB%A1%9C%EA%B7%B8%EB%9E%A8.zip)**

ZIP을 모두 압축 해제하고 `이노베이스_GPS_프로그램.bat`을 실행하세요. Python 3.12 64비트가 필요합니다. 자세한 설치·키 설정·샘플·전체 폴더 분석 명령은 [Windows 실행 순서](gps-analyzer/WINDOWS_실행순서.md)에 있습니다.

API 키, 실제 `.env`, GPS 원본·분석 결과·캐시는 저장소와 배포 ZIP에 포함하지 않습니다. 현재 실증 범위는 하루 샘플과 자동 테스트63개입니다. Windows 실기·실제 API 인증·전체2GB 처리는 별도 검증 대상입니다.

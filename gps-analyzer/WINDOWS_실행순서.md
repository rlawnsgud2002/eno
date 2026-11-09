# Windows 실행 순서

스마트 앱 컨트롤 때문에 배치 파일이 차단되면 [PowerShell 직접 설치·실행](POWERSHELL_실행순서.md)을 사용하세요. 보호 기능과 실행 정책을 변경하지 않습니다.


명령 프롬프트(CMD)에서 실행합니다. Python 3.12 64비트가 필요합니다.
GitHub에서 Code → Download ZIP으로 받은 뒤 전체를 압축 해제하고,
install.bat이 보이는 폴더에서 주소창에 cmd를 입력해 터미널을 여세요.

## 1. 설치

```bat
install.bat
```

## 2. API 키 설정

.env가 처음 만들어지는 경우에만 복사하고 메모장으로 실제 키를 입력합니다.
기존 .env가 있으면 덮어쓰지 마세요. 키를 GitHub에 올리지 마세요.

```bat
if not exist .env copy .env.example .env
notepad .env
```

VWORLD_API_KEY, KAKAO_REST_API_KEY, DATA_GO_KR_SERVICE_KEY 중 보유한 키를 입력합니다.
VWORLD_SERVICE_URL은 등록된 http://localhost:8000을 유지합니다.
API 키가 없어도 3분 이상 GPS 체류 분석과 미확인 장소 보고서는 실행됩니다.

## 3. 샘플 실행 (외부 API 기본 꺼짐)

D:\GPS샘플 폴더에 본인이 보유한 ASAN_29_2026_06_11.xlsx를 넣습니다.
원본은 다운로드 ZIP과 GitHub에 포함하지 않습니다.

```bat
run.bat "D:\GPS샘플"
set "GPS_SAMPLE_FILE=D:\GPS샘플\ASAN_29_2026_06_11.xlsx"
test.bat
```

## 4. V-World 실조회 검증

```bat
verify_vworld.bat
```

output\vworld_연동검증.json의 query_verified, schema_ready가 true인지 확인합니다.
불투명 속성명은 README의 안내에 따라 공식 레이어 설명과 대조하고 config.json에 매핑합니다.
키/도메인 제한이나 필드 미확인일 때 성공으로 간주하지 않습니다.
클라우드에서는 인증 헤더를 위조하지 않고 Windows 로컬에서 검증합니다.

검증한 API를 일반 분석에서도 쓸 때는 아래 파일의 api.enabled를 true로 변경합니다.
키 없는 분석을 계속하려면 false를 유지합니다.

```bat
notepad config.json
```

## 5. 전체 폴더 분석

원본 21대 차량의 Excel 파일들을 프로그램 폴더 밖 D:\GPS원본에 넣습니다.
하위 폴더도 자동 검색합니다. 경로는 실제 PC 환경에 맞게 바꾸세요.

```bat
run.bat "D:\GPS원본"
```

## 6. 최종 보고서 확인

```bat
start "" "output\아산시_21대차량_2026년4월~8월_방문장소_통합분석.xlsx"
start "" "output"
```

보고서는 실행할 때마다 최종 결과로 갱신합니다. 이전 결과가 필요하면 먼저 복사하세요.
실패 원인은 work_data\analysis.log와 run_status.json에 기록됩니다.
전체 건수·누락 차량/월·API 조회 상태는 output\validation.json에서 확인합니다.
이번 검증은 하루 샘플과 Linux의 자동 테스트63개이며 Windows 실기 및 실API 성공은 미확인입니다.

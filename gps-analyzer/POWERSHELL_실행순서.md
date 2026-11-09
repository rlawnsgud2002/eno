# PowerShell에서 Python 소스로 설치·실행

이 절차는 Windows 스마트 앱 컨트롤을 켠 상태에서 사용할 수 있는 표준 Python 실행 방법입니다. 배치 파일이나 PowerShell 스크립트 파일을 실행하지 않고, 열린 PowerShell 창에 명령을 한 줄씩 입력합니다. 보호 기능이나 실행 정책은 변경하지 않습니다. GPS 프로그램을 독립 실행 EXE로 패키징하지 않았으며, 아래 `python.exe`는 설치한 Python 인터프리터입니다.

**실행 가능 여부는 실제로 차단된 파일에 달려 있습니다.** Python 자체 또는 의존성의 DLL/PYD까지 Windows가 차단하면 이 절차로 실행이 보장되지는 않습니다. 이때 차단을 무시하거나 파일 이름을 바꾸지 말고, 차단 알림의 파일명과 공식 배포본의 서명·출처를 확인하세요. Microsoft의 [Smart App Control 안내](https://support.microsoft.com/en-us/windows/security/threat-malware-protection/smart-app-control-frequently-asked-questions)를 참고하세요. 이번 검증 환경은 Linux이며 Windows 스마트 앱 컨트롤을 켠 PC에서 실기 검증한 것은 아닙니다.

## 1. Python과 작업 폴더 준비

- **Python 3.12 64비트**가 필요합니다. 32비트나 다른 버전으로 설치된 환경은 이번 검증 대상이 아닙니다.
- Microsoft Store의 Python Software Foundation 제공 Python 3.12 또는 [python.org의 공식 Windows 설치본](https://www.python.org/downloads/windows/)을 사용하세요. Python 자체 설치는 기존 `install.bat`이 자동 수행하지 않습니다.
- ZIP 전체를 압축 해제하세요. 기존 `이노베이스_GPS_프로그램.zip`은 그대로 제공하며, 문서를 추가한 배포본은 `이노베이스_GPS_프로그램_PowerShell안내.zip`입니다. 두 ZIP의 GPS 실행 소스·배치 파일·자동 테스트는 같습니다.
- `requirements.txt`, `config.json`, `gps_analyzer` 폴더가 함께 보이는 **압축 해제 폴더**에서 PowerShell을 여세요. GitHub의 Code → Download ZIP으로 받았다면 `gps-analyzer` 하위 폴더가 프로그램 폴더입니다.
- 폴더를 이동할 때는 실제 경로를 따옴표로 감싸세요. 예: `Set-Location -LiteralPath 'C:\GPS\이노베이스_GPS_프로그램'`. 원본 GPS는 별도 폴더에 보관합니다.

현재 폴더와 Python을 확인합니다.

```powershell
Get-Location
Test-Path -LiteralPath '.\requirements.txt'
py -3.12 --version
py -3.12 -c "import sys, struct; print(sys.executable); print('bits:', struct.calcsize('P') * 8); assert sys.version_info[:2] == (3, 12) and struct.calcsize('P') == 8, 'Python 3.12 64-bit required'"
```

`Test-Path`는 `True`, 버전은 `Python 3.12.x`, `bits`는 `64`여야 합니다. `py` 명령이 없고 `python`만 있다면 다음 명령으로 확인합니다.

```powershell
python --version
python -c "import sys, struct; print(sys.executable); print('bits:', struct.calcsize('P') * 8); assert sys.version_info[:2] == (3, 12) and struct.calcsize('P') == 8, 'Python 3.12 64-bit required'"
```

어느 경우든 오류·보안 차단이 보이면 다음 단계로 계속 진행하지 마세요. Python이 없으면 공식 설치를 완료하고 새 PowerShell 창을 여세요.

## 2. 가상환경과 의존성 설치

`py -3.12` 확인에 성공했다면 다음을 실행합니다.

```powershell
py -3.12 -m venv .venv
if ($LASTEXITCODE -ne 0) { throw 'Virtual environment creation failed. Stop here.' }
```

`py` 대신 위에서 확인한 `python`을 사용하는 경우에만, 위 두 줄 대신 다음을 실행합니다.

```powershell
python -m venv .venv
if ($LASTEXITCODE -ne 0) { throw 'Virtual environment creation failed. Stop here.' }
```

이후 명령은 두 경우 모두 동일합니다. PowerShell의 `&`를 포함해 입력하세요.

```powershell
& '.\.venv\Scripts\python.exe' -m pip install -r '.\requirements.txt'
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed. Stop here.' }
& '.\.venv\Scripts\python.exe' -c "import duckdb, openpyxl, xlsxwriter, shapely, requests, dotenv; print('Dependency check OK')"
if ($LASTEXITCODE -ne 0) { throw 'Dependency import failed. Stop here.' }
```

필요한 패키지는 DuckDB, openpyxl, XlsxWriter, Shapely, requests, python-dotenv이며 버전 범위는 `requirements.txt`에 있습니다. 가상환경 활성화 스크립트는 실행하지 않아도 됩니다. 프로젝트 폴더 안의 Python을 직접 지정하므로 관리자 권한이나 전역 패키지 설치도 필요하지 않습니다. 설치에는 패키지 다운로드를 위한 인터넷 연결이 필요합니다.

## 3. API 키 설정 — 키 없이 먼저 분석해도 됨

`.env`가 없을 때만 예제 파일을 복사합니다. 기존 키는 덮어쓰지 않습니다.

```powershell
if (-not (Test-Path -LiteralPath '.\.env')) { Copy-Item -LiteralPath '.\.env.example' -Destination '.\.env' }
notepad.exe '.\.env'
```

메모장에서 아래 이름에 본인이 발급받은 값만 입력하고 저장하세요. 키를 PowerShell 명령줄이나 GitHub에 입력하지 않습니다.

```dotenv
KAKAO_REST_API_KEY=
DATA_GO_KR_SERVICE_KEY=
VWORLD_API_KEY=
VWORLD_SERVICE_URL=http://localhost:8000
```

보유하지 않은 키는 빈 값으로 두면 됩니다. 기본 `config.json`의 `api.enabled`는 `false`입니다. 이 상태에서도 GPS 정제·체류 후보 탐지·7개 시트 Excel은 생성되며 장소는 미확인으로 남을 수 있습니다. 실제 `.env`와 GPS 원본·결과 보고서·캐시는 배포 ZIP이나 GitHub에 넣지 않습니다.

## 4. 샘플 하나로 실행

예를 들어 `D:\GPS샘플` 폴더에는 본인이 가진 `ASAN_29_2026_06_11.xlsx` 하나만 넣으세요. 아래 원본 경로는 실제 PC의 폴더로 바꾸세요. 프로그램은 원본을 읽기만 합니다.

```powershell
& '.\.venv\Scripts\python.exe' -m gps_analyzer --input 'D:\GPS샘플' --output '.\output' --work '.\work_data' --config '.\config.json'
if ($LASTEXITCODE -ne 0) { throw 'Sample analysis failed. Check work_data\analysis.log.' }
```

샘플 테스트를 포함한 기존 자동 테스트63개를 실행하려면:

```powershell
$env:GPS_SAMPLE_FILE = 'D:\GPS샘플\ASAN_29_2026_06_11.xlsx'
& '.\.venv\Scripts\python.exe' -m unittest discover -s '.\tests' -v
```

`GPS_SAMPLE_FILE`이 실제 파일을 가리키지 않으면 실제 샘플 테스트4개는 건너뜁니다. `Ran 63 tests`뿐 아니라 `OK`와 건너뜀 여부를 함께 확인하세요. 실제 샘플 테스트 시 해당 입력 폴더에는 그 샘플만 있어야 합니다.

## 5. V-World 직접 검증

Windows 로컬 환경에서 다음 명령을 실행합니다. 기존 `verify_vworld.bat`과 같은 Python 모듈을 직접 실행하는 명령입니다.

```powershell
& '.\.venv\Scripts\python.exe' -m gps_analyzer.vworld --config '.\config.json' --output '.\output\vworld_연동검증.json'
Get-Content -LiteralPath '.\output\vworld_연동검증.json' -Encoding UTF8
```

종료 코드0과 `query_verified=true`, `schema_ready=true`를 확인합니다. 코드2는 키 없음·빈 검색·속성 매핑 미완료·인증 제한 등으로 아직 실조회/스키마 확인을 완료하지 못했다는 뜻입니다. `status`와 `schema_issues`를 확인하세요. 등록된 서비스 URL은 `http://localhost:8000`을 유지하며 인증 헤더 위조나 클라우드 우회를 하지 않습니다. 불투명한 속성 이름은 README의 V-World 설명에 따라 공식 스키마와 대조하여 명시적으로 매핑합니다.

API를 실제 일반 분석에 적용하려면 검증 후 메모장으로 `config.json`의 `api.enabled`를 `true`로 변경하세요. GPS만 분석하려면 `false`를 유지합니다.

```powershell
notepad.exe '.\config.json'
```

## 6. 전체 GPS 원본 폴더 분석

원본 21대·5개월 Excel을 프로그램 폴더 밖의 전용 폴더에 넣습니다. 예시 `D:\GPS원본`을 실제 경로로 바꾸세요. 입력과 출력 폴더를 같게 지정하지 않습니다. 하위 폴더도 자동 검색하고 파일별로 순차 처리하며 완료된 캐시를 재사용합니다.

```powershell
& '.\.venv\Scripts\python.exe' -m gps_analyzer --input 'D:\GPS원본' --output '.\output' --work '.\work_data' --config '.\config.json'
if ($LASTEXITCODE -ne 0) { throw 'Analysis failed. Check work_data\analysis.log.' }
```

## 7. 최종 Excel과 로그 열기

```powershell
Invoke-Item -LiteralPath '.\output\아산시_21대차량_2026년4월~8월_방문장소_통합분석.xlsx'
Get-Content -LiteralPath '.\output\validation.json' -Encoding UTF8
Get-Content -LiteralPath '.\work_data\analysis.log' -Encoding UTF8 -Tail 50
```

최종 Excel은 7개 기본 시트와 차트를 포함합니다. 보고서는 다음 실행 때 갱신되므로 필요한 이전 결과는 먼저 복사하세요. 누락된 차량/월과 API 조회 상태는 `validation.json`, 마지막 실행 상태는 `work_data/run_status.json`에서 확인합니다. 실행 실패 뒤 남아 있는 Excel은 이전 결과일 수 있습니다.

이번 변경은 실행 문서와 배포 ZIP만 보완한 것으로 GPS 알고리즘·63개 테스트·기존 배치 파일을 변경하지 않습니다. Windows 실기·스마트 앱 컨트롤·실제 API 키 인증·전체 2GB 데이터 처리는 별도 검증 대상입니다.

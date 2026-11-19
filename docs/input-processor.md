# 입력처리기 명세

사용자/외부 입력을 검증·정규화해 챗봇이 처리할 수 있는 형태로 변환한다.

## 인터페이스

- `process(raw) -> ProcessResult` (`chatbot/input_processor.py`)
- 성공: `ok=True`, `text`=정규화된 문자열
- 실패: `ok=False`, `error`=`ErrorCode` (예외를 던지지 않는다)
- 사용자 안내 문구: `chatbot/messages.py`의 `REJECTION_MESSAGES` (모든 `ErrorCode`에 하나씩)

## 처리 순서

유형 검사 → 디코딩 → 인코딩 검증 → 정규화 → 빈 입력 검사 → 길이 검사

## 검증 규칙

| 단계    | 규칙                                                     | 실패 코드          |
| ------- | -------------------------------------------------------- | ------------------ |
| 유형    | `str`, `bytes`만 허용                                    | `INVALID_TYPE`     |
| 디코딩  | bytes는 UTF-8로 디코딩, 실패 시 거부                     | `INVALID_ENCODING` |
| 인코딩  | UTF-8로 인코딩할 수 없는 str(lone surrogate 포함)은 거부 | `INVALID_ENCODING` |
| 빈 입력 | 정규화 후 빈 문자열이면 거부                             | `EMPTY`            |
| 길이    | 정규화 후 문자 수 > 4000 이면 거부                       | `TOO_LONG`         |

## 정규화 규칙

### 제거하는 문자 (보이지 않거나 악용될 수 있는 문자)

| 대상 | 문자 | 이유 |
| ---- | ---- | ---- |
| C0 제어문자 | `\x00`–`\x1f` (`\t`, `\n` 제외. `\r`·ESC·BEL은 아래 단계에서 처리) | 화면에 안 보임, 터미널 제어 |
| DEL, C1 제어문자 | `\x7f`, `\x80`–`\x9f` (NEL `\x85` 제외) | 화면에 안 보임 |
| zero-width, BOM | `\u200b`, `\ufeff` | 웹·파일 복사로 섞여 들어옴 |
| 기타 서식 문자 | `\u00ad` soft hyphen, `\u180e`, `\u2060`–`\u2064`, `\ufff9`–`\ufffb` | 화면에 안 보임 |
| 방향 제어 | `\u202a`–`\u202e` (embedding/override), `\u2066`–`\u2069` (isolate) | 화면 표시 순서를 뒤집어 사람이 보는 내용과 실제 내용을 다르게 만듦 |
| 태그 문자 | `\U000e0000`–`\U000e007f` | 화면에 안 보이지만 LLM은 읽음 → 숨은 프롬프트 인젝션(ASCII smuggling) |
| 빈 글자처럼 보이는 문자 | `\u3164`, `\uffa0`, `\u115f`, `\u1160` (한글 채움), `\u2800` (점자 빈칸) | 메신저의 "빈 메시지" 트릭 |
| 터미널 제어 시퀀스 | CSI `ESC [ … 최종바이트` (예: `\x1b[31m`), OSC `ESC ] … (BEL 또는 ESC \)` (예: 하이퍼링크) | 시퀀스 전체를 지운다. ESC만 지우면 `[31m` 같은 잔해가 남는다 |

### 유지하는 보이지 않는 문자

| 문자 | 이유 |
| ---- | ---- |
| `\u200d` ZWJ, `\ufe0f` 등 변이 선택자 | 이모지 결합 (👨‍👩‍👧, ❤️, 키캡) |
| `\u200c` ZWNJ | 페르시아어·인도계 문자 철자 |
| `\u200e`, `\u200f` LRM/RLM | 방향 표시만 하고 순서를 뒤집지 않음 |
| 정식 지역 깃발 태그 시퀀스 | 잉글랜드·스코틀랜드·웨일스 깃발. 🏴 + 태그 + 종료 태그 형태 중 이 셋만 유지 |
| ESC 없는 대괄호 텍스트 (`[31m]`, `a[0]`) | 제어 시퀀스가 아님 |

### 바꾸는 문자

| 대상 | 결과 |
| ---- | ---- |
| `\r\n`, `\r`, `\x85` NEL, `\u2028`, `\u2029` | `\n` |
| 특수 공백 (유니코드 `Zs`: `\u00a0` NBSP, `\u3000` 전각 공백 등 16종) | 일반 공백. 연속 공백을 줄이지는 않음 |
| 조합형(NFD) 한글 등 | NFC (NFKC는 쓰지 않음: `①`→`1`처럼 의미가 바뀜) |
| 앞뒤 공백 | 제거 (중간 공백·들여쓰기는 유지) |

### 정규화 내부 순서

보이지 않는 문자 제거 → 태그 문자 제거 → OSC·CSI 시퀀스 제거 → 남은 ESC·BEL 제거 → 줄바꿈 통일 → 특수 공백 → NFC → 앞뒤 공백 제거

보이지 않는 문자는 처음부터 없었던 것으로 취급한다. 다른 단계보다 먼저 지워야 하는 이유:

- NFD 자모 사이에 끼어 있으면 NFC가 자모를 합치지 못한다 (예: `ㅇ\u200bㅏㄴ` → `안`이 되어야 함)
- `\r`과 `\n` 사이에 끼어 있으면 줄바꿈이 두 개로 처리된다 (예: `\r\x00\n` → 줄바꿈 하나)
- 제어 시퀀스 안에 끼어 있으면 시퀀스를 알아보지 못해 잔해가 남는다 (예: `\x1b\u200b[31m` → 전부 제거)

앞의 두 경우는 eval 무작위 케이스(`evals/generate_random_cases.py`, seed 42·7)에서 발견했다.
ESC·BEL·`\r`·NEL은 제어 시퀀스와 줄바꿈의 일부라서 첫 단계에서 지우지 않고 해당 단계에서 처리한다.

### 항상 성립하는 성질 (`tests/test_input_processor_properties.py`)

- 어떤 입력에도 예외를 던지지 않는다
- 통과한 결과는 1자 이상 4000자 이하, 앞뒤 공백 없음, NFC, 위 "제거하는 문자"가 없음
- 멱등성: 통과한 결과를 다시 넣으면 그대로 나온다
- 같은 내용의 UTF-8 bytes와 str은 같은 결과를 낸다

## 알려진 한계

- 인코딩이 잘못 해석됐지만 surrogate 없이 정상 문자로 깨진 입력(예: `é`가 cp949로 읽혀 `챕`)은 탐지할 수 없다.
  근본 해결은 입력을 읽는 쪽의 인코딩 설정이다 (`main.py`의 `sys.stdin.reconfigure(encoding="utf-8")`).
- 종료되지 않은 OSC 시퀀스(`\x1b]0;제목`만 있고 끝 표시가 없음)는 ESC만 지워지고 나머지는 남는다.

## 로깅

로거 `chatbot.input_processor`. 원문(입력 텍스트)은 어떤 레벨에서도 기록하지 않는다.

| 상황 | 레벨 | message | 필드 |
| ---- | ---- | ------- | ---- |
| 거부 | INFO | `input rejected` | `error_code`, `input_type`, `raw_length`(str/bytes가 아니면 `null`) |
| 통과 | DEBUG | `input accepted` | `input_type`, `raw_length`, `normalized_length`, `changed`(정규화가 내용을 바꿨는지) |

앱(`main.py`)은 `logs/chatbot.log`에 JSON Lines로 남긴다(5MB × 5개 회전, 모든 줄에 `session_id`). `LOG_LEVEL=DEBUG`는 `chatbot.*` 로거에만 적용한다
(외부 라이브러리의 DEBUG 로그에는 대화 내용이 들어갈 수 있으므로).

입력·응답 원문은 운영 로그가 아니라 트레이스(`traces/`)에 따로 남긴다 → `docs/tracing.md`

## 검증

| 종류 | 위치 | 실행 |
| ---- | ---- | ---- |
| 예시 기반 단위 테스트 (TDD) | `tests/test_input_processor.py` | `pytest` |
| 속성 기반 테스트 | `tests/test_input_processor_properties.py` | `pytest` |
| eval 데이터셋 + 게이트 (EDD) | `evals/input_cases.jsonl`, `evals/random_cases.jsonl` | `python evals/run_input_eval.py` |
| 무작위 eval | `evals/generate_random_cases.py` | `--seed N --count M --out 파일` 후 실행기에 전달 |
| CI | `.github/workflows/ci.yml` | push·PR마다 위 전부 + seed 5개 × 300건 |

## 범위 밖

- 명령어(`/exit` 등) 분류
- 토큰 단위 길이 제한

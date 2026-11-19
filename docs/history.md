# 작업 기록 (History)

Claude Agent SDK 기반 터미널 챗봇에 **입력처리기**를 TDD + EDD(eval-driven development)로 만든 과정의 기록이다.
날짜는 모두 2026-11-19. 규칙 자체는 `docs/input-processor.md`, 트레이스는 `docs/tracing.md`를 본다.

## 한눈에 보기

| 단계 | 내용 | 결과 |
| ---- | ---- | ---- |
| 0 | 환경 구성, 챗봇 뼈대 | venv, Claude Agent SDK, 대화형 루프 |
| 1 | 입력처리기 TDD | 유형·인코딩·정규화·빈 입력·길이 규칙, 단위 테스트 |
| 2 | EDD 도입 | eval 데이터셋·실행기·게이트·무작위 생성기 → **순서 버그 2건 발견** |
| 3 | 로깅 | 원문 없는 구조화 로그, 거부 안내 메시지, 앱 연결 |
| 4 | 명세 보강 | NBSP·ANSI, 숨은 문자(태그·방향 제어 등), OSC, 줄 구분자 |
| 5 | 품질 장치 | 속성 기반 테스트, mutation 확인, CI |
| 6 | 트레이스 | 원문 기록(운영 로그와 분리), eval 후보 추출 → 하네스 연결 고리 |

최종 상태: 테스트 142개, eval 126개(손으로 만든 66 + 무작위 60) GATE PASSED, CI에서 무작위 1,500건 추가 검증.

---

## 0. 환경 구성과 챗봇 뼈대

| 커밋 | 내용 |
| ---- | ---- |
| `c5ea38d` | 저장소 시작: `.gitignore`(`.venv/`), `CLAUDE.md` |
| `1d43ca9` | Claude Agent SDK로 대화형 챗봇 (`ClaudeSDKClient`, `tools=[]`, `setting_sources=[]`) |

- API 키 대신 로컬 Claude Code 로그인을 쓰는 `claude-agent-sdk`를 선택.
- 처음 겪은 문제
  - `msg.blocks` → `msg.content` 오타, 실패 후 SDK 정리 과정이 멈춰 Ctrl+C traceback처럼 보였음.
  - Windows 콘솔(cp949)이 이모지를 출력하지 못함 → `sys.stdout.reconfigure(encoding="utf-8")`.
  - `query()`는 한 번 묻고 끝남 → 대화 유지는 `ClaudeSDKClient`.
  - **Git Bash 파이프에서 한글 입력이 `\udcec…`처럼 깨짐(stdin이 cp949)** → 이후 입력처리기의 출발점이 됨.

## 1. 입력처리기 TDD

명세(`docs/input-processor.md`)를 먼저 정하고 Red → Green → Refactor를 한 규칙씩 반복했다.

| 커밋 | 규칙 |
| ---- | ---- |
| `1eeadc2` | 앞뒤 공백 제거, 빈 입력 거부(`EMPTY`), 결과 객체 `ProcessResult` + `ErrorCode` |
| `5f74a63` | 유형 검사(`INVALID_TYPE`), bytes UTF-8 디코딩(`INVALID_ENCODING`) |
| `653660e` | lone surrogate가 섞인 str 거부 (Git Bash에서 실제로 본 깨진 입력) |
| `6880d33` | 리팩터링: 단계별 함수 + 내부 예외 `_Rejected` → `process`가 명세 순서대로 읽힘 |
| `c879e57` | NFC 정규화 (NFKC는 `①`→`1`처럼 의미를 바꾸므로 제외) |
| `ab1e223` | 줄바꿈 통일 `\r\n`, `\r` → `\n` |
| `11deb28`, `049d7e5` | 제어문자·zero-width 제거, ZWJ·변이 선택자는 유지 (이모지 👨‍👩‍👧 보존) |
| `49a8e01` | 정규화 후 4000자 초과 거부(`TOO_LONG`), 경계값 테스트 |

배운 것
- Red는 "실패"가 아니라 **예상한 이유로 실패**하는 것. `ImportError`/`NameError`로 실패하면 아직 Red가 아니다.
- 처음부터 통과하는 회귀 방지 테스트는 Red 단계에서 아무것도 검증하지 않는다 → 테스트 데이터를 직접 계산해서 확인(`crlf` 경계 케이스 퀴즈).
- 명세에서 "하지 않기로 한 것"(NFKC 미사용, 중간 공백 유지)도 테스트로 고정한다.

## 2. EDD 도입과 eval이 찾은 버그

| 커밋 | 내용 |
| ---- | ---- |
| (evals/) | `input_cases.jsonl`(손으로 단 라벨), `generate_random_cases.py`(깨끗한 문장 + 결과를 아는 노이즈), `run_input_eval.py`(PASS/FAIL/REVIEW/ERROR, 지표, 게이트, 실행 기록 비교) |
| `ce57f25` | **버그 1** (seed 42): NFD 자모 사이에 보이지 않는 문자가 끼면 NFC가 합치지 못함 → 제거를 NFC 앞으로 |
| `cbd960b` | `.gitignore` 마지막 줄에 줄바꿈이 없어 규칙이 붙어 버린 문제 정리 (캐시·실행 기록 추적 해제) |
| `6ab388c` | **버그 2** (seed 7): `\r`과 `\n` 사이에 보이지 않는 문자가 끼면 줄바꿈이 두 개 → 제거를 줄바꿈 통일 앞으로, `\r`은 제거 대상에서 제외 |
| `1ef14de` | 태그별 정확도에서 REVIEW(정답 미정) 케이스를 분모에서 제외 |

배운 것
- 라벨은 `process()` 결과가 아니라 **사람·명세**가 정한다. 그래야 eval이 무언가를 검증한다.
- TDD는 정한 규칙을 지키고, EDD는 **규칙끼리 부딪히는 곳**을 찾는다. 두 버그 모두 단계별 단위 테스트로는 안 보이던 순서 문제였다.
- 흐름: eval FAIL → 최소 재현 케이스로 줄이기 → 단위 테스트 Red → Green → eval 재실행(FAIL → PASS).
- FAIL이면 무조건 코드를 고치는 게 아니라, 라벨과 코드 중 무엇이 틀렸는지 판단한다 (REVIEW → 명세 결정).

## 3. 로깅과 앱 연결

| 커밋 | 내용 |
| ---- | ---- |
| `0e2a40d` | 거부를 INFO로 기록 (`error_code`, `input_type`, `raw_length`). 기록 지점은 `except _Rejected` 한 곳 |
| `1bb1ec2` | 원문이 로그에 남지 않음을 테스트로 고정. mutation으로 테스트 검증 → UTF-16 케이스가 비밀을 못 잡는 구멍 발견·수정 |
| `7b6c7de` | `chatbot/messages.py`: 모든 `ErrorCode`에 사용자 안내 문구 (Enum 순회 parametrize) |
| `0b5e38f` | `JsonFormatter`(JSON Lines), `setup_logging()` — `basicConfig`가 pytest에서 조용히 무시되는 함정 회피, 로그 폴더 자동 생성 |
| `f98588f` | `main.py` 연결: 입력처리기, 거부 메시지, Ctrl+C/EOF, `exit` 판정은 정규화된 텍스트로, stdin UTF-8, traceback 기록, 통과 입력 DEBUG 로그, `LOG_LEVEL`은 `chatbot.*`에만 |

배운 것
- 테스트는 "메시지가 있는가"는 검증하지만 "도움이 되는가"는 사람이 판단한다 (`"..."` 자리표시도 통과함).
- 외부 라이브러리 DEBUG 로그에는 대화 내용이 섞일 수 있어 로그 레벨은 우리 로거에만 적용.

## 4. 명세 보강

| 커밋 | 내용 |
| ---- | ---- |
| `13d3762` | 특수 공백(유니코드 `Zs`, NBSP 등) → 일반 공백, ANSI CSI 시퀀스 통째로 제거. REVIEW 2건 해소 |
| `f0c8292` | 태그 문자(숨은 프롬프트 인젝션), 방향 제어(표시 순서 뒤집기), C1·서식 문자, 한글 채움·점자 빈칸("빈 메시지" 트릭) 제거. 잉글랜드 등 정식 깃발 시퀀스·ZWNJ·LRM은 유지. OSC 시퀀스(하이퍼링크) 제거, `\u2028`·`\u2029`·NEL → `\n` |

배운 것
- 명세가 바뀌면 **eval 라벨을 만드는 규칙(생성기의 노이즈 가정)도 바뀐다.** ESC·BEL은 제어 시퀀스의 일부가 되면서 "아무 데나 끼워도 되는 노이즈"에서 빠졌다.
- 실제 출력에서 나오지 않는 입력(시퀀스 안의 시퀀스)은 생성기에서 만들지 않는다.

## 5. 품질 장치

| 커밋 | 내용 |
| ---- | ---- |
| `b3dfa5e` | hypothesis 속성 테스트: 예외 없음, 출력 불변식, 멱등성, bytes/str 동일성. mutation 5개로 확인 (길이 경계는 예시 테스트가 담당) |
| `f698c79` | eval 케이스 66개(실제 Git Bash 관찰 3건 포함), 생성기 노이즈 9종, 무작위 6,000건 PASS |
| `965603f` | CI(GitHub Actions): pytest + eval 게이트 + seed 5개 × 300건. `CLAUDE.md` 정리 |

## 6. 트레이스 (하네스 준비)

| 커밋 | 내용 |
| ---- | ---- |
| `e854318` | 모든 로그 줄에 `session_id`(외부 라이브러리 로그 포함), 로그 파일 회전(5MB × 5) |
| `92cafdc` | `chatbot/tracing.py`: 턴마다 입력·응답 원문, 정규화 결과, 모델, 토큰, 비용, 버전(git 커밋, 프롬프트 해시)을 `traces/`에 기록. `logging`을 거치지 않음. `TRACE=0`으로 끄기, 30일 보관. 공용 직렬화 `chatbot/jsonl.py` |
| `63cc4f5` | `evals/traces_to_cases.py`: 트레이스 → eval 후보(`needs-review`). `docs/tracing.md` |

결정
- 원문은 **운영 로그가 아니라 트레이스에만** 남긴다. 두 기록은 `session_id`로 잇는다.
- 트레이스에서 뽑은 후보의 라벨은 현재 구현의 결과일 뿐이라 사람이 검토한 뒤 eval로 옮긴다.

---

## 작업 중 겪은 도구·환경 문제

- **cp949**: Windows 콘솔 출력, Git Bash stdin 파이프, 기본 파일 인코딩 모두에서 문제 → 출력·입력·로그 파일 모두 UTF-8을 명시.
- **이스케이프 표기 변환**: 셸·도구를 거치며 `\u200b` 같은 표기가 실제 보이지 않는 문자로 바뀌어 소스·문서에 들어간 일이 있었다 → 문자열·주석 안의 보이지 않는 문자를 이스케이프로 되돌리는 검사를 돌리고, 규칙으로 남김("소스·테스트·문서의 보이지 않는 문자는 항상 `\uXXXX`").
- **`.gitignore`**: 파일 끝 줄바꿈이 없으면 `echo >>`가 같은 줄에 이어 붙는다.

## 남은 일

- EDD 레벨 2: 입력처리기를 거친 입력이 응답 품질에 주는 영향을 LLM으로 채점 (비용 발생)
- 트레이스 기반 하네스 루프 자동화: 실패 후보 자동 선별(사용자 재질문 등 신호), 주기적 실행
- 슬래시 명령어(`/help`, `/clear`), 토큰 단위 길이 제한 (명세상 범위 밖)
- `main.py` 흐름(트레이스 기록 포함)의 자동 테스트, 배포 시 트레이스 동의·마스킹 규칙
- CI의 Node.js 20 기반 액션 버전 갱신

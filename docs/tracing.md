# 트레이스 명세

품질 평가와 eval 데이터 수집을 위해 대화 원문을 기록한다. 하네스(지속적 품질 개선 루프)의 입력이다.

```
실사용 → traces/ → evals/traces_to_cases.py → evals/candidates.jsonl → 사람이 검토 → evals/input_cases.jsonl → eval 게이트(CI)
```

## 운영 로그와의 구분

| | 운영 로그 | 트레이스 |
| - | - | - |
| 위치 | `logs/chatbot.log` (5MB × 5개 회전) | `traces/YYYY-MM-DD.jsonl` (UTC 날짜별) |
| 내용 | 메타데이터만. **원문 금지** (테스트로 강제) | 입력·응답 **원문 포함** |
| 경로 | `logging` 모듈 | `chatbot/tracing.py`의 `TraceWriter` (`logging`을 거치지 않음 → 원문이 운영 로그로 새지 않음) |
| 켜기/끄기 | 항상 켜짐. `LOG_LEVEL`로 레벨 조절 | 기본 켜짐. `TRACE=0`이면 끔 |
| 보관 | 회전 | `TRACE_RETENTION_DAYS`(기본 30일)가 지난 파일은 시작할 때 삭제 |
| git | 제외 (`logs/`) | 제외 (`traces/`, `evals/candidates.jsonl`) |

두 기록은 `session_id`로 잇는다. 운영 로그의 모든 줄(외부 라이브러리 로그 포함)에 같은 `session_id`가 붙는다.

## 레코드

모든 레코드에 `time`(UTC ISO 8601)이 붙는다. 보이지 않는 문자와 lone surrogate는 `\uXXXX`로 이스케이프된다(`chatbot/jsonl.py`). bytes는 `{"bytes_hex": "..."}`.

### `session_start`

| 필드 | 설명 |
| - | - |
| `session_id` | 앱이 만든 세션 ID (12자리) |
| `git_commit`, `git_dirty` | 어떤 코드로 대화했는지. 커밋하지 않은 변경이 있으면 `git_dirty: true` |
| `prompt_version` | 시스템 프롬프트의 SHA-256 앞 12자리 |
| `system_prompt` | 시스템 프롬프트 원문 |

### `turn` (사용자 입력 한 번마다. `exit`/`quit`은 기록하지 않음)

| 필드 | 설명 |
| - | - |
| `session_id`, `turn` | 세션 안에서 1부터 증가 |
| `input.raw` | 사용자가 입력한 원문 (깨진 입력 포함) |
| `input.normalized`, `input.ok`, `input.error` | 입력처리기 결과 |
| `output` | 거부되지 않았을 때만. `text`(응답 원문), `model`, `stop_reason`, `is_error`, `num_turns`, `duration_ms`, `duration_api_ms`, `cost_usd`, `usage`(토큰), `sdk_session_id` |
| `error` | SDK 오류로 응답을 받지 못했을 때 `"오류종류: 메시지"` |

### `session_end`

`session_id`, `turns`(기록된 턴 수)

## eval 후보 추출

`python evals/traces_to_cases.py` (옵션: `--all`, `--since YYYY-MM-DD`)

- 기본은 **거부된 입력**과 **정규화로 바뀐 입력**만 뽑는다. 그대로 통과한 입력까지 원하면 `--all`.
- 이미 `input_cases.jsonl`이나 `candidates.jsonl`에 있는 입력은 건너뛴다.
- 후보의 `expect`/`normalized`는 **현재 구현의 결과를 채운 제안**이다. `label_status: "needs-review"`가 붙는다.
  사람이 맞는 동작인지 판단해 고친 뒤 `input_cases.jsonl`로 옮기고 `label_status`를 지운다.
  제안을 검토 없이 옮기면 그 케이스는 아무것도 검증하지 못한다.

## 주의

- 트레이스에는 사용자가 입력한 내용이 그대로 남는다. 개인정보·비밀번호가 들어갈 수 있으므로 공유·커밋하지 않는다.
- 다른 사람이 쓰는 환경에 배포한다면 기본값을 `TRACE=0`으로 바꾸고, 동의와 마스킹 규칙을 먼저 정한다.

# LLM 호출기 명세

프롬프트 구성기가 완성한 입력(`Prompt`)을 받아 지정된 모델 API를 호출하고, **원시 응답(raw response)을 손대지 않고** 다음 단계로 넘긴다.

```
입력처리기 → 프롬프트 구성기 → [LLM 호출기] → 출력처리기 → 사용자
              Prompt ──────────→ 원시 이벤트(스트림) + RawResponse ──→
```

**핵심 원칙: 모델을 호출하는 코드는 비즈니스 로직, 응답 파싱, 사용자에게 돌려주는 코드와 한 함수에 섞이지 않는다.**

## 책임

| 하는 것 | 하지 않는 것 (누구의 몫인가) |
| ------- | ---------------------------- |
| `Prompt` → 공급자 호출 형태로 변환 | 프롬프트 조립 → 프롬프트 구성기 |
| 호출, 세션 수명, 타임아웃, 재시도 | 응답 파싱(텍스트 블록 추출 등), 변환, 내용 검증 → 출력처리기 |
| 오류를 공통 예외로 분류 | 화면 출력(`print`) → 출력처리기 / 앱 |
| 지연·사용량·비용 **측정** | 입력 거부, `exit` 판정, 트레이스 레코드 조립 → `main` |
| 원시 이벤트를 도착 순서대로, 원본 객체 그대로 전달 | 응답이 "쓸 만한지" 판단 (`stop_reason`이 길이 제한이어도 오류가 아니다) |

### 경계선: "전송 상태"와 "내용"

호출기가 원시 응답을 들여다보는 것은 **계측과 전송 상태 판단**에 한한다.

- 들여다봐도 되는 것:
  - 첫 출력 이벤트가 도착한 시각
  - 사용량, 비용, 응답한 모델 이름 (계기판 읽기)
  - 호출 자체의 실패 신호 (HTTP 상태, SDK `ResultMessage.is_error` + `api_error_status`)
- 들여다보지 않는 것: 텍스트, 도구 호출, `stop_reason`의 의미. 이것들은 출력처리기가 해석한다.

## 인터페이스 (`chatbot/llm_caller.py`)

```python
class LLMCaller:                       # 모든 구현의 공통 모양
    provider: str                      # "claude-agent-sdk", "anthropic-messages", "fake" ...
    model: str | None                  # 요청할 모델 (None = 공급자 기본값)

    async def __aenter__(self) -> "LLMCaller": ...   # 세션/연결 열기 (없으면 아무것도 안 함)
    async def __aexit__(self, *exc) -> None: ...     # 닫기

    def stream(self, prompt: Prompt) -> CallStream: ...
```

사용하는 쪽(`main`)은 이 모양만 안다:

```python
async with caller:                               # 세션은 호출기가 소유한다
    call = caller.stream(prompt)
    async for event in call:                     # 원시 이벤트. 출력처리기가 소비한다
        ...
    raw = call.response                          # 다 받은 뒤: RawResponse (이벤트 전체 + 측정값)
```

- **`CallStream`**: 원시 이벤트의 async iterator.
  - 끝까지 소비한 뒤에만 `.response`를 읽을 수 있다. 소비 전에 읽으면 `RuntimeError`.
  - 한 번만 순회할 수 있다.
- **`async with` 밖에서 `stream()`을 부르면 `RuntimeError`.** 세션을 열지 않은 호출은 프로그래밍 오류다.
- 한 세션 안에서 호출은 **순차적**이다. 이전 스트림을 다 소비하기 전에 새 `stream()`을 부르면 `RuntimeError`.

### `RawResponse`

```python
@dataclass(frozen=True)
class RawResponse:
    provider: str
    requested_model: str | None
    events: tuple                # 공급자 객체 원본, 도착 순서 그대로 (변환·복사하지 않음)
    metrics: CallMetrics
```

`events`의 요소는 공급자마다 타입이 다르다. Agent SDK는 `AssistantMessage`, `ResultMessage` 등이고, Messages API는 스트리밍 이벤트 객체다. 다음 단계가 공급자 타입을 알아야 한다는 뜻이다. 그래서 출력처리기도 공급자별 파서를 갖게 된다(4부). 호출기가 공통 형태로 바꾸면 그것이 곧 파싱이므로 하지 않는다.

### `CallMetrics` (측정값. 내용은 담지 않는다)

| 필드 | 뜻 |
| ---- | -- |
| `latency_ms` | 호출 시작부터 마지막 이벤트까지. 재시도 대기 시간 포함 |
| `time_to_first_output_ms` | 호출 시작부터 첫 **출력 이벤트**까지. 출력 이벤트가 없으면 `None` |
| `attempts` | 시도 횟수 (재시도 없으면 1) |
| `response_model` | 실제로 응답한 모델 이름 (공급자가 알려 주면) |
| `usage` | 공급자가 준 토큰 사용량을 **복사한 dict** (키 이름도 공급자 그대로) |
| `cost_usd` | 비용. 공급자가 주면 그 값, 아니면 `None` |
| `cost_source` | `"provider"` / `"price_table"` / `None` |

- **출력 이벤트**의 정의는 구현마다 하나씩 둔다(`_is_output_event`).
  - Agent SDK: 첫 `AssistantMessage`. 부분 메시지를 켜지 않았으므로 토큰 단위가 아니라 메시지 단위다. 그래서 이름을 TTFT가 아니라 `time_to_first_output_ms`로 했다.
  - Messages API: 첫 `content_block_delta`.
- 비용: Messages API는 `usage`만 준다. 단가표로 계산할지는 C5에서 정한다. 그 전까지는 `cost_usd=None`이다. 추정값을 실측처럼 기록하지 않는다.

## 측정 지점: 한 곳

재시도, 타임아웃, 측정, 로깅은 **공통 기반 클래스 한 곳**(`BaseCaller.stream`)에만 있다. 구현은 공급자 사정만 채운다.

| 구현이 채우는 것 | 뜻 |
| ---------------- | -- |
| `_open(prompt) -> AsyncIterator[event]` | 시도 1회: 공급자 호출을 시작하고 원시 이벤트를 내보낸다 |
| `_classify(exc) -> (kind, retryable)` | 공급자 예외 → 공통 오류 분류 |
| `_is_output_event(event) -> bool` | 첫 출력 시각을 잴 이벤트 |
| `_meter(events) -> dict` | `response_model`, `usage`, `cost_usd`를 읽어 옴(계기판 읽기) |
| `_check_status(events)` | 이벤트 안의 전송 실패 신호를 예외로 (선택) |
| `_connect()` / `_close()` | 세션 수명 (선택) |

- 시계는 주입한다: `clock`(단조 시계, 초 단위, 기본 `time.monotonic`)과 `sleep`(기본 `anyio.sleep`). `TraceWriter`의 `now`처럼 테스트에서 지연 값을 정확히 단언할 수 있다.
- 공급자를 바꿔도 측정 코드는 그대로다. 공급자별로 다른 것은 "어느 이벤트가 첫 출력인가"와 "사용량이 어디 있나"뿐이다.

## 공급자별 변환 (차이는 구현 안에 가둔다)

| | Agent SDK (`chatbot/callers/agent_sdk.py`) | Messages API (C5 후보) |
| - | - | - |
| 세션 | `__aenter__`에서 `ClaudeSDKClient` 연결, `__aexit__`에서 종료 | 없음 (HTTP 클라이언트만) |
| `system` | 세션을 열 때 `options.system_prompt`로 한 번 전달 | 요청마다 `system=` |
| `messages` | 마지막 user 메시지만 `query()` (기록은 세션이 보관) | 전체를 `messages=` |
| 인증 | 로컬 Claude Code 로그인 | `ANTHROPIC_API_KEY` (`.env`, 커밋 금지) |
| 오류 원천 | `ClaudeSDKError` 계열 예외 + `ResultMessage.is_error` | HTTP 상태별 예외 |

- **Agent SDK 고유 규칙**:
  - 세션의 시스템 지시는 바꿀 수 없다. `prompt.system`이 세션을 열 때의 값과 다르면 호출하지 않고 `LLMCallError(kind="bad_request")`를 낸다. 조용히 무시하면 시스템 지시를 바꾼 실험이 적용되지 않은 채 기록된다.
  - `Prompt`의 대화 기록은 보내지 않는다. 세션 기록과 어긋날 수 있다는 한계로 남겨 둔다.
- 생성 시 필요한 값(시스템 지시, 모델, 세션 옵션)은 생성자로 받는다. `main`은 `ClaudeAgentOptions`를 모른다.

## 오류 (`LLMCallError`)

호출기 바깥으로는 **공급자 예외가 새어 나가지 않는다.** 모든 실패는 `LLMCallError`다.

```python
class LLMCallError(Exception):
    kind: str                    # 아래 표
    retryable: bool              # 일시 오류인가 (재시도 여부는 이것과 "첫 이벤트 전인가"로 결정)
    attempts: int
    partial: RawResponse | None  # 일부 이벤트를 받은 뒤 실패했으면 그때까지의 이벤트 + 측정값
    # __cause__ = 원래 공급자 예외
```

| `kind` | 예 | 기본 `retryable` |
| ------ | -- | ---------------- |
| `timeout` | 시도 시간 초과 | 예 |
| `rate_limit` | 429, SDK `RateLimitEvent` 후 실패 | 예 |
| `server` | 5xx, 529 과부하 | 예 |
| `connection` | 네트워크 끊김 | 예 (단, Agent SDK의 CLI 프로세스 종료는 같은 세션으로 회복할 수 없어 아니오) |
| `auth` | 401/403, 로그인 안 됨 | 아니오 |
| `bad_request` | 400, 시스템 지시 불일치 | 아니오 |
| `unavailable` | CLI 없음(`CLINotFoundError`), 설정 오류 | 아니오 |
| `unknown` | 분류되지 않은 예외 | 아니오 |

- `retryable`은 구현이 정한다. 같은 `kind`라도 공급자 사정에 따라 다를 수 있다.
- `KeyboardInterrupt`, `asyncio.CancelledError`는 분류하지 않고 그대로 통과시킨다. 취소는 오류가 아니다.

### 타임아웃과 재시도

- 타임아웃: 시도마다 `timeout_s`(기본 120초). 넘으면 `kind="timeout"`.
- 재시도 조건(모두 만족):
  1. `retryable`이 참이다.
  2. **소비자에게 아직 이벤트를 하나도 내보내지 않았다.** 이미 화면에 일부가 나갔다면 다시 호출하지 않는다. 출력 중복과 비용 중복을 막기 위해서다. 이때는 `partial`을 담아 실패한다.
  3. 시도 횟수가 `max_attempts`(기본 3 = 최초 1 + 재시도 2) 미만이다.
- 대기: 지수 백오프 `backoff_s * 2**(n-1)`(기본 1초, 2초). 공급자가 `retry-after`를 주면 그 값을 쓴다. 대기는 주입한 `sleep`으로 한다.
- 실패한 시도도 과금될 수 있다. `attempts`를 측정값으로 남겨 비용 분석에 쓴다.

## 로깅 (원문 금지)

호출기는 자기 메타데이터만 운영 로그에 남긴다. 프롬프트·응답 원문은 어떤 레벨에서도 남기지 않는다(`*_never_logged` 테스트).

| 메시지 | 레벨 | `extra` |
| ------ | ---- | ------- |
| `llm call started` | DEBUG | `provider`, `model`, `attempt` |
| `llm event received` | DEBUG | `event_type` (클래스 이름만) |
| `llm call retrying` | WARNING | `kind`, `attempt`, `delay_s` |
| `llm call completed` | INFO | `provider`, `response_model`, `latency_ms`, `time_to_first_output_ms`, `attempts`, `cost_usd`, `usage`(숫자만) |
| `llm call failed` | WARNING | `kind`, `retryable`, `attempts` (스택은 `main`이 `logger.exception`으로) |

## 트레이스 (C3에서 확정)

- `turn` 레코드에 `call` 필드를 추가한다. 내용: `provider`, `requested_model`, `metrics` 전체. 실패했으면 `error_kind`도 넣는다.
- 원시 이벤트 전체를 트레이스에 남길지는 C3에서 정한다(크기 문제). 응답 원문은 지금처럼 `output`에 남지만, 출력처리기가 만든 값을 `main`이 넣는다.
- 레코드 조립은 `main`이 한다. 호출기는 트레이서를 모른다.

## 설정 (C5)

- 공급자와 모델은 코드가 아니라 설정으로 고른다: `LLM_PROVIDER`(기본 `claude-agent-sdk`), `LLM_MODEL`(기본 없음).
- `create_caller(provider, model, system, ...) -> LLMCaller` 팩토리 한 곳에서만 구현 클래스를 안다.
- `session_start` 트레이스의 `version`에 `provider`와 `requested_model`을 넣는다. 모델이 바뀐 시점을 추적하기 위해서다.

## 검증

- **계약 테스트**(`tests/test_llm_caller_contract.py`): 모든 구현(가짜, Agent SDK 어댑터, 이후 Messages API)이 같은 묶음을 통과해야 한다. 실제 네트워크 없이 공급자 객체를 가짜로 바꿔 돌린다.
  1. 공급자가 낸 이벤트를 같은 순서, **같은 객체**(`is`)로 내보낸다.
  2. `response.events`가 내보낸 이벤트 전체와 같다.
  3. 받은 `Prompt`를 변경하지 않는다. 공급자에 전달된 내용이 의도한 것이다(구현별 기대값).
  4. 주입한 시계로 `latency_ms`, `time_to_first_output_ms`가 정확한 값이다.
  5. 공급자 예외는 항상 `LLMCallError`로 바뀌고 `__cause__`가 보존된다.
  6. 일시 오류는 첫 이벤트 전이면 재시도하고, 첫 이벤트 후면 재시도하지 않고 `partial`과 함께 실패한다.
  7. 일시 오류가 아니면 재시도하지 않는다. 시도 횟수는 `max_attempts`를 넘지 않는다.
  8. `async with` 밖 호출, 소비 전 `.response`, 겹친 호출은 `RuntimeError`다.
  9. 원문이 운영 로그에 남지 않는다.
- **EDD**(C7):
  - 레벨 1: 가짜 호출기로 측정·오류·재시도 경로를 시나리오 케이스로 결정적으로 검증한다(비용 0, CI 게이트).
  - 레벨 2: 비용 승인 후 실제 모델로 지연·비용 기준선을 기록한다. 모델을 교체할 때 같은 케이스로 v1과 v2를 비교한다.

## 범위 밖

- 응답 파싱·검증·출력 → 출력처리기(4부)
- 도구 호출 실행 루프 (지금 챗봇은 도구를 쓰지 않는다)
- 대화 기록을 누가 보관하는가(Messages API로 바꾸면 `main`이 기록을 들고 `build_prompt(history=...)`에 넘겨야 한다) → C5/C6에서 다룬다
- 토큰 단위 부분 메시지 스트리밍 (Agent SDK `include_partial_messages`) → 필요해지면 구현 안에서만 바꾼다

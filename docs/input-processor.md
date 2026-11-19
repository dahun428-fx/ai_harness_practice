# 입력처리기 명세

사용자/외부 입력을 검증·정규화해 챗봇이 처리할 수 있는 형태로 변환한다.

## 인터페이스

- `process(raw) -> ProcessResult`
- 성공: `ok=True`, `text`=정규화된 문자열
- 실패: `ok=False`, `error`=오류 코드 (예외를 던지지 않는다)

## 처리 순서

유형 검사 → 디코딩 → 인코딩 검증 → 정규화 → 빈 입력 검사 → 길이 검사

## 규칙

| 단계    | 규칙                                                                       | 실패 코드          |
| ------- | -------------------------------------------------------------------------- | ------------------ |
| 유형    | `str`, `bytes`만 허용                                                      | `INVALID_TYPE`     |
| 디코딩  | bytes는 UTF-8로 디코딩, 실패 시 거부                                       | `INVALID_ENCODING` |
| 인코딩  | UTF-8로 인코딩할 수 없는 str(lone surrogate 포함)은 거부                   | `INVALID_ENCODING` |
| 정규화  | NFC 적용                                                                   | -                  |
| 정규화  | `\r\n`, `\r` → `\n`                                                        | -                  |
| 정규화  | 제어문자 제거: `\x00`–`\x1f`, `\x7f` (단 `\n`, `\t` 유지, `\r`은 줄바꿈 통일에서, ESC는 ANSI 처리에서) | -                  |
| 정규화  | zero-width 제거: `\u200b`(ZWSP), `\ufeff`(BOM)                             | -                  |
| 정규화  | ANSI 제어 시퀀스(CSI: `ESC [ … 최종바이트`, 예: `\x1b[31m`) 통째로 제거. ESC가 없는 `[31m` 같은 텍스트는 유지 | -                  |
| 정규화  | **유지**: `\u200d`(ZWJ), `\ufe0f`(variation selector) — 이모지 시퀀스 보존 | -                  |
| 정규화  | 특수 공백(유니코드 `Zs`: `\u00a0` NBSP, `\u3000` 전각 공백 등) → 일반 공백. 연속 공백을 줄이지는 않음 | -                  |
| 정규화  | 앞뒤 공백 제거 (중간 공백 유지)                                            | -                  |
| 빈 입력 | 정규화 후 빈 문자열이면 거부                                               | `EMPTY`            |
| 길이    | 정규화 후 문자 수 > 4000 이면 거부                                         | `TOO_LONG`         |

> 정규화 내부 순서: 보이지 않는 문자 제거(`\r`, ESC 제외) → ANSI 시퀀스 제거 → 남은 ESC 제거 → 줄바꿈 통일 → 특수 공백 → NFC → 앞뒤 공백 제거.
> 보이지 않는 문자는 처음부터 없었던 것으로 취급한다. 다른 단계보다 먼저 지워야 하는 이유:
>
> - NFD 자모 사이에 끼어 있으면 NFC가 자모를 합치지 못한다 (예: `ㅇ\u200bㅏㄴ` → `안`이 되어야 함)
> - `\r`과 `\n` 사이에 끼어 있으면 줄바꿈이 두 개로 처리된다 (예: `\r\x00\n` → 줄바꿈 하나)
> - ANSI 시퀀스 안에 끼어 있으면 시퀀스를 알아보지 못해 잔해가 남는다 (예: `\x1b\u200b[31m` → 전부 제거)
>
> 앞의 두 경우는 eval 무작위 케이스(`evals/generate_random_cases.py`, seed 42·7)에서 발견했다.

## 로깅

로거 `chatbot.input_processor`. 원문(입력 텍스트)은 어떤 레벨에서도 기록하지 않는다.

| 상황 | 레벨 | message | 필드 |
| ---- | ---- | ------- | ---- |
| 거부 | INFO | `input rejected` | `error_code`, `input_type`, `raw_length`(str/bytes가 아니면 `null`) |
| 통과 | DEBUG | `input accepted` | `input_type`, `raw_length`, `normalized_length`, `changed`(정규화가 내용을 바꿨는지) |

앱(`main.py`)은 `logs/chatbot.log`에 JSON Lines로 남긴다. `LOG_LEVEL=DEBUG`는 `chatbot.*` 로거에만 적용한다
(외부 라이브러리의 DEBUG 로그에는 대화 내용이 들어갈 수 있으므로).

## 범위 밖

- 명령어(`/exit` 등) 분류
- 토큰 단위 길이 제한

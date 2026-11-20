"""프롬프트 구성기.

시스템 지시, 대화 기록, 사용자 입력을 조합해 LLM에 전달할 최종 입력(Prompt)을 만든다.
- 순수 함수로만 구성한다(파일 읽기는 load_prompt_spec 한 곳). 같은 입력이면 항상 같은 출력이 나와야
  스냅샷 테스트와 프롬프트 캐싱이 가능하다.
- 결과는 특정 SDK에 묶이지 않은 중립 형태(Prompt)다. SDK 호출 형태로 바꾸는 일은 어댑터가 맡는다.
"""

# Sequence: list, tuple 등 "순서가 있고 인덱스로 접근할 수 있는 것"을 모두 받는 추상 타입
from collections.abc import Sequence

# dataclass: 필드만 선언하면 __init__, __eq__, __repr__을 자동으로 만들어 주는 데코레이터
from dataclasses import dataclass

# Path: 운영체제와 무관하게 파일 경로를 다루는 객체 (Windows의 역슬래시 / POSIX의 슬래시 차이를 감춘다)
from pathlib import Path

# Template: "$name" / "${name}" 자리표시를 치환하는 표준 라이브러리 템플릿 (새 의존성 없음)
from string import Template

# Literal: 값이 정해진 몇 개 중 하나라는 걸 타입 힌트로 표현 (role은 "user" 또는 "assistant"만)
from typing import Literal


from xml.sax.saxutils import escape

# PyYAML: 프롬프트 파일(prompts/*.yaml)을 읽는 데 쓰는 외부 라이브러리
import yaml

# ---------------------------------------------------------------------------
# 예외
# ---------------------------------------------------------------------------


# 프롬프트를 조립하는 단계(대화 기록 규칙, 예산)에서 생긴 문제.
# ValueError를 상속해 "인자로 받은 값이 잘못됐다"는 의미를 유지한다.
class PromptBuildError(ValueError):
    pass


# 템플릿 치환이나 프롬프트 파일(YAML) 내용에 문제가 있을 때 쓰는 예외.
class TemplateError(ValueError):
    pass


# ---------------------------------------------------------------------------
# 데이터 모델
# ---------------------------------------------------------------------------


# frozen=True: 만든 뒤에는 필드를 바꿀 수 없다(불변).
# 여러 곳에서 같은 메시지 객체를 공유해도 누군가 몰래 고칠 수 없고, 해시도 가능해진다.
@dataclass(frozen=True)
class Message:
    # 메시지를 보낸 쪽. system은 메시지 목록이 아니라 Prompt.system에 따로 둔다.
    role: Literal["user", "assistant"]
    # 메시지 본문
    content: str


# 모델에 전달할 최종 입력. SDK와 무관한 중립 형태다.
@dataclass(frozen=True)
class Prompt:
    # 시스템 지시. 사용자 입력은 절대 여기에 들어가지 않는다(역할 경계).
    system: str
    # 대화 기록 + 현재 사용자 입력. list가 아니라 tuple이라 불변이고, == 비교로 결정성을 검사할 수 있다.
    messages: tuple[Message, ...]
    # 예산 때문에 버린 대화 턴(user+assistant 쌍)의 수. 기본값 0이라 기존 호출은 그대로 동작한다.
    dropped_turns: int = 0


# 프롬프트 파일(YAML) 한 개의 내용.
@dataclass(frozen=True)
class PromptSpec:
    # 프롬프트 식별자. 트레이스의 prompt_id로 남는다.
    id: str
    # 사람이 읽는 설명
    description: str
    # 템플릿이 쓰는 변수 이름 목록. 로딩할 때 실제 템플릿과 일치하는지 검사한다.
    variables: tuple[str, ...]
    # 시스템 지시 템플릿 (아직 변수가 치환되지 않은 원본)
    system: str


@dataclass(frozen=True)
class Document:
    source: str
    content: str


# 프롬프트 파일에 반드시 있어야 하는 키와 각 값의 타입.
# 이 dict 하나가 "필수 키 목록", "허용 키 목록", "타입 규칙" 세 가지 역할을 한다.
_SPEC_FIELDS = {"id": str, "description": str, "variables": list, "system": str}


# ---------------------------------------------------------------------------
# 프롬프트 조립
# ---------------------------------------------------------------------------


def _size(text: str) -> int:
    """예산 계산 단위. 지금은 글자 수(코드포인트 수, UTF-8 바이트 수가 아님). 나중에 토큰 수로 바꿀 자리."""
    # 한글 "안녕"은 UTF-8로 6바이트지만 len()은 2를 돌려준다.
    return len(text)


def _check_alternation(history: Sequence[Message]) -> None:
    """대화 기록이 user로 시작해 user/assistant가 번갈아 나오고 assistant로 끝나는지 검사한다."""
    # enumerate: (위치, 값) 쌍을 차례로 돌려준다
    for i, message in enumerate(history):
        # 짝수 위치(0, 2, 4…)는 user, 홀수 위치(1, 3, 5…)는 assistant여야 교대가 된다
        expected = "user" if i % 2 == 0 else "assistant"
        # 기대한 역할과 다르면 교대 규칙 위반 (assistant로 시작, user 연속 등)
        if message.role != expected:
            raise PromptBuildError(
                f"history must alternate user/assistant: message {i} is '{message.role}', expected '{expected}'"
            )
    # 위 반복문은 (user,)처럼 user로 끝나는 기록을 통과시킨다. 0번 위치에서 user가 기대값이기 때문이다.
    # 길이가 홀수면 마지막이 user라는 뜻이고, 현재 입력(user)과 user가 연달아 붙게 되므로 따로 막는다.
    if len(history) % 2:
        raise PromptBuildError("history must alternate user/assistant and end with assistant (ends with user)")


def _render_documents(documents: Sequence[Document]) -> str:
    """문서들을 <documents> 블록으로 감싼다. 문서가 없으면 빈 문자열(→ user 메시지는 기존과 동일)."""
    if not documents:
        return ""
    parts = ["<documents>"]
    for index, document in enumerate(documents, start=1):
        # 속성값은 큰따옴표로 감싸므로 " 도 이스케이프해야 속성을 위조할 수 없다
        source = escape(document.source, {'"': "&quot;"})
        # 본문의 </documents> 같은 태그가 블록을 닫지 못하게 < > & 를 이스케이프한다
        parts.append(f'<document index="{index}" source="{source}">\n{escape(document.content)}\n</document>')
    parts.append("</documents>")
    return "\n".join(parts) + "\n\n"


def build_prompt(
    system: str,
    user_input: str,
    history: Sequence[Message] = (),
    documents: Sequence[Document] = (),
    max_chars: int | None = None,
) -> Prompt:
    """시스템 지시 + 대화 기록 + 현재 입력으로 Prompt를 만든다.

    max_chars가 주어지면 system을 포함한 전체 글자 수가 그 이하가 되도록 가장 오래된 턴부터 버린다.
    현재 입력은 절대 버리지 않는다.
    """
    # 교대 검사는 예산 처리보다 먼저 한다. 잘못된 기록을 먼저 자르면 문제가 우연히 가려질 수 있다.
    _check_alternation(history)

    # 기록을 (user, assistant) 쌍의 리스트로 묶는다. 예: [u1, a1, u2, a2] → [(u1, a1), (u2, a2)]
    # 버릴 때 쌍 단위로 버려야 "질문 없는 답"이 맨 앞에 남지 않는다.
    turns = [tuple(history[i : i + 2]) for i in range(0, len(history), 2)]

    # 버린 턴 수를 센다. 예산이 없으면 0으로 남는다.
    dropped = 0

    # 이번 턴의 user 메시지 = 문서 블록(문서가 없으면 빈 문자열) + 사용자 입력.
    # 문서를 질문 앞에 둔다. 문서는 신뢰하지 않는 외부 데이터라 system이 아니라 user 쪽에 넣는다.
    current = _render_documents(documents) + user_input

    # max_chars=None이면 자르지 않는다(기본 동작).
    if max_chars is not None:
        # 무슨 일이 있어도 들어가야 하는 부분: 시스템 지시 + 문서 블록 + 현재 입력.
        # 이스케이프된 뒤의 크기로 센다(< 는 &lt; 로 4자가 된다). 모델이 실제로 받는 것이 그 텍스트다.
        fixed = _size(system) + _size(current)
        # 이것만으로 예산을 넘으면 기록을 아무리 버려도 맞출 수 없다 → 조용히 자르지 않고 오류.
        # 메시지에는 내용이 아니라 길이만 넣는다. 이 예외가 운영 로그에 남아도 원문이 새지 않게 하기 위해서다.
        if fixed > max_chars:
            raise PromptBuildError(f"system + documents + current input ({fixed} chars) exceed budget {max_chars}")
        # 남은 기록까지 합친 전체 크기가 예산을 넘는 동안 반복한다. ">"이므로 예산과 딱 같으면 통과한다.
        while turns and fixed + sum(_size(m.content) for t in turns for m in t) > max_chars:
            # 맨 앞 = 가장 오래된 턴(user+assistant 쌍)을 버린다
            turns.pop(0)
            # 버린 수를 하나 올린다
            dropped += 1

    # 남은 쌍들을 다시 한 줄로 펼치고(이중 for), 맨 뒤에 이번 턴의 user 메시지를 붙인다.
    # 사용자 입력(과 문서)은 messages에만 들어가고 system에는 들어가지 않는다.
    messages = tuple(m for t in turns for m in t) + (Message(role="user", content=current),)
    return Prompt(system=system, messages=messages, dropped_turns=dropped)


# ---------------------------------------------------------------------------
# 템플릿
# ---------------------------------------------------------------------------


def render_template(text: str, variables: dict[str, str]) -> str:
    """템플릿의 $name / ${name} 자리를 variables 값으로 한 번만 치환한다."""
    # 문자열을 템플릿 객체로 만든다
    template = Template(text)
    # 템플릿이 실제로 쓰는 변수 이름들. get_identifiers()는 Python 3.11+에서 쓸 수 있다.
    # $1처럼 잘못된 자리표시는 여기서 조용히 빠지고, 아래 substitute()에서 ValueError로 잡힌다.
    expected = set(template.get_identifiers())

    # 템플릿에는 있는데 값을 안 넘긴 변수. 그대로 두면 "$name"이 모델에 그대로 전달된다.
    missing = expected - variables.keys()
    if missing:
        # sorted: set의 순서는 실행마다 다를 수 있으므로 정렬해서 메시지를 결정적으로 만든다
        raise TemplateError(f"Missing variables: {', '.join(sorted(missing))}")
    # 값은 넘겼는데 템플릿이 쓰지 않는 변수. 호출 쪽 오타(nmae)나 템플릿에서 변수를 지운 뒤의 드리프트를 잡는다.
    unused = variables.keys() - expected
    if unused:
        raise TemplateError(f"Unused variables: {', '.join(sorted(unused))}")

    try:
        # 치환은 한 번만 일어난다. 값 안에 "$b"가 있어도 다시 치환되지 않는다 → 변수끼리 값이 새지 않는다.
        # "$$"는 문자 "$" 하나가 된다.
        return template.substitute(variables)
    except ValueError as e:
        # "$1", "100$ 할인"처럼 자리표시 문법이 틀린 경우. from e로 원래 예외를 이어 붙여 원인 추적을 돕는다.
        raise TemplateError(f"Template substitution error: {e}") from e


def load_prompt_spec(path: str | Path) -> PromptSpec:
    """프롬프트 파일(YAML)을 읽고 검증해 PromptSpec으로 돌려준다. 문제가 있으면 TemplateError."""
    try:
        # read_text는 텍스트 모드라 CRLF 줄바꿈을 LF로 바꿔 읽는다(universal newlines).
        # safe_load: 기본 타입(str, int, list, dict…)만 만든다. yaml.load는 !!python 태그로 임의 코드를 실행할 수 있다.
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        # 들여쓰기 오류, 괄호 미닫힘, 허용되지 않은 태그 등 YAML 문제를 모두 우리 예외로 감싼다
        raise TemplateError(f"{path}: YAML parsing error: {e}") from e
    # 최상위가 키-값 구조(dict)가 아니면(빈 파일 → None, 리스트 등) 더 볼 것이 없다
    if not isinstance(data, dict):
        raise TemplateError(f"{path}: expected a YAML mapping, got {type(data).__name__}")

    # 필수 키 중 파일에 없는 것
    missing = _SPEC_FIELDS.keys() - data.keys()
    if missing:
        raise TemplateError(f"{path}: missing required keys: {', '.join(sorted(missing))}")
    # 파일에 있지만 허용하지 않는 키 (sytem 같은 오타). YAML은 오타 키를 조용히 받아들이므로 직접 막는다.
    # 바다코끼리 연산자(:=)로 계산과 동시에 변수에 담고, 비어 있지 않으면 오류.
    if unknown := data.keys() - _SPEC_FIELDS.keys():
        # YAML 키는 문자열이 아닐 수 있다(1: x → int 키). map(str)로 바꿔야 sorted/join이 TypeError를 내지 않는다.
        raise TemplateError(f"{path}: unknown keys: {', '.join(sorted(map(str, unknown)))}")

    # 각 키의 값 타입 검사. YAML 1.1에서는 no → False, 1.0 → float처럼 의도와 다른 타입이 될 수 있다.
    for key, kind in _SPEC_FIELDS.items():
        if not isinstance(data[key], kind):
            raise TemplateError(f"{path}: key '{key}' must be of type {kind.__name__}, got {type(data[key]).__name__}")
    # variables는 list인 것만으로는 부족하고, 원소가 모두 문자열이어야 한다
    if not all(isinstance(v, str) for v in data["variables"]):
        raise TemplateError(f"{path}: all elements in 'variables' must be strings")

    # YAML의 | 블록은 끝에 줄바꿈을 남긴다. 끝 공백을 지워야 편집기 차이로 프롬프트(와 해시)가 바뀌지 않는다.
    system = data["system"].rstrip()
    # 파일에 선언된 변수
    declared = set(data["variables"])
    # 템플릿이 실제로 쓰는 변수
    used = set(Template(system).get_identifiers())
    # 둘이 다르면 앱 시작 시점에 실패시킨다. render_template의 검사보다 이르게(첫 대화 전에) 잡는 것이 목적이다.
    if declared != used:
        raise TemplateError(
            f"{path}: variables mismatch: undeclared {sorted(used - declared)}, unused {sorted(declared - used)}"
        )

    # 검증을 모두 통과한 값으로 불변 객체를 만든다. list → tuple로 바꿔 불변성을 유지한다.
    return PromptSpec(
        id=data["id"],
        description=data["description"],
        variables=tuple(data["variables"]),
        system=system,
    )

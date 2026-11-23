"""프롬프트 구성기.

시스템 지시, 예시, 외부 문서, 대화 기록, 사용자 입력을 조합해 LLM에 전달할 최종 입력(Prompt)을 만든다.
- 순수 함수로만 구성한다(파일 읽기는 load_prompt_spec 한 곳). 같은 입력이면 항상 같은 출력이 나와야
  스냅샷 테스트와 프롬프트 캐싱이 가능하다.
- 결과는 특정 SDK에 묶이지 않은 중립 형태(Prompt)다. SDK 호출 형태로 바꾸는 일은 어댑터가 맡는다.
- 신뢰 수준에 따라 자리가 다르다.
    system   : 우리가 쓴 것(prompts/*.yaml의 지시와 예시). 신뢰한다.
    messages : 사용자 입력과 외부 문서. 신뢰하지 않는다. 절대 system에 넣지 않는다.
"""

# hashlib: sha256 등 해시 함수. 프롬프트 정의가 바뀌었는지 짧은 지문(spec_version)으로 비교하는 데 쓴다.
import hashlib

# json: 해시할 내용을 키 순서·공백이 고정된 문자열로 바꾸는 데 쓴다(정규화).
import json

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

# escape: & < > 를 &amp; &lt; &gt; 로 바꾸는 XML 이스케이프. 두 번째 인자로 추가 치환 규칙을 줄 수 있다.
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
    # 대화 기록 + 이번 턴의 user 메시지. list가 아니라 tuple이라 불변이고, == 비교로 결정성을 검사할 수 있다.
    messages: tuple[Message, ...]
    # 예산 때문에 버린 대화 턴(user+assistant 쌍)의 수. 기본값 0이라 기존 호출은 그대로 동작한다.
    dropped_turns: int = 0
    # 현재 사용자 메시지에 넣은 외부 문서의 수
    document_count: int = 0

    def metadata(self) -> dict:
        """턴마다 기록할 개수·크기 정보. 내용은 절대 담지 않는다(운영 로그에도 남길 수 있게)."""
        return {
            # 모델에 보내는 메시지 수 (system 제외)
            "message_count": len(self.messages),
            # 예산 때문에 버린 대화 턴 수
            "dropped_turns": self.dropped_turns,
            # 이번 턴에 붙인 문서 수
            "document_count": self.document_count,
            # 예산과 같은 단위(_size)로 센다. 나중에 토큰 단위로 바꿔도 예산과 기록이 어긋나지 않는다.
            # _size는 이 클래스보다 아래에 정의돼 있지만 괜찮다: 이름은 메서드가 "호출될 때" 찾는다.
            "total_chars": _size(self.system) + sum(_size(m.content) for m in self.messages),
        }


# 외부 문서(검색 결과, 파일 내용 등). 우리가 쓰지 않은, 신뢰하지 않는 텍스트다.
@dataclass(frozen=True)
class Document:
    # 출처 (파일 이름, URL 등). <document source="..."> 속성으로 들어간다.
    source: str
    # 본문. 이스케이프된 뒤 <document> 블록 안에 들어간다.
    content: str


# few-shot 예시 한 쌍. prompts/*.yaml의 examples 항목 하나에 해당한다. 우리가 쓴, 신뢰하는 텍스트다.
@dataclass(frozen=True)
class Example:
    # 예시 질문
    user: str
    # 예시 답변 (모델이 따라 했으면 하는 형식과 말투)
    assistant: str


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
    # 예시 목록. 선택 항목이라 기본값이 빈 tuple이고, 예시가 없으면 system은 예전과 바이트 단위로 같다.
    examples: tuple[Example, ...] = ()


# ---------------------------------------------------------------------------
# 상수
# ---------------------------------------------------------------------------

# 프롬프트 파일에 반드시 있어야 하는 키와 각 값의 타입
_REQUIRED_FIELDS = {"id": str, "description": str, "variables": list, "system": str}
# 있어도 되고 없어도 되는 키와 각 값의 타입. 필수 + 선택 = 허용되는 키 전체(나머지는 오타로 보고 거부).
_OPTIONAL_FIELDS = {"examples": list}
# 예시 블록에 쓰는 태그. 예시 본문에 이 닫는 태그가 있으면 블록 구조가 깨지므로 로딩할 때 거부한다.
# "</examples>"는 "</example>"을 부분 문자열로 포함하지 않으므로(s> 와 >) 순서는 상관없다.
_EXAMPLE_TAGS = ("examples", "example", "user", "assistant")


# ---------------------------------------------------------------------------
# 시스템 지시 렌더링 (템플릿 + 예시)
# ---------------------------------------------------------------------------


def spec_version(spec: PromptSpec) -> str:
    """프롬프트 정의 중 '모델이 받는 텍스트를 결정하는 것'만 해시한 버전 문자열(12자리).

    - system(템플릿)과 examples만 넣는다. id, description은 모델이 보지 않으므로 뺀다.
    - 렌더링 결과가 아니라 템플릿을 해시한다. ${today} 같은 변수 값이 바뀌어도 버전은 그대로다.
    - 파일 바이트가 아니라 파싱한 내용을 해시한다. YAML 주석·따옴표·들여쓰기는 버전에 영향이 없다.
    - 조립 규칙 코드(태그 형식 등)의 변화는 여기서 잡지 않는다. git_commit과 스냅샷 테스트가 맡는다.
    """
    # 해시할 내용. 튜플/dataclass는 JSON으로 바꿀 수 없으므로 dict 리스트로 펼친다.
    content = {"system": spec.system, "examples": [{"user": e.user, "assistant": e.assistant} for e in spec.examples]}
    # 정규화: 키 순서 고정(sort_keys), 공백 없는 구분자, 한글을 \uXXXX로 바꾸지 않음(ensure_ascii=False).
    # 지금은 dict를 늘 같은 순서로 만들어 sort_keys가 없어도 결과가 같지만, 키 순서를 바꾸는 리팩터링에 대비한 방어다.
    canonical = json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    # sha256 → 16진수 문자열 → 앞 12자리 (tracing.prompt_version과 같은 길이)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]


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


def _render_examples(examples: Sequence[Example]) -> str:
    """예시들을 <examples> 블록으로 만든다. 예시가 없으면 빈 문자열(→ system은 템플릿 결과와 동일)."""
    # 예시가 없으면 구분용 빈 줄도 붙이지 않는다. 그래야 기존 프롬프트와 해시(prompt_version)가 같다.
    if not examples:
        return ""
    # 여는 태그부터 시작해 예시를 하나씩 덧붙인다
    parts = ["<examples>"]
    for example in examples:
        # 예시는 우리가 쓴 텍스트라 이스케이프하지 않는다(HTML 예시 등이 망가지지 않게).
        # 구조를 깨는 닫는 태그는 _parse_examples가 로딩 시점에 이미 막았다.
        parts.append(
            f"<example>\n<user>\n{example.user}\n</user>\n<assistant>\n{example.assistant}\n</assistant>\n</example>"
        )
    # 닫는 태그
    parts.append("</examples>")
    # 템플릿 본문과 빈 줄 하나로 구분한다. 구분자를 여기서 붙여야 예시가 없을 때 빈 줄이 생기지 않는다.
    return "\n\n" + "\n".join(parts)


def render_system(spec: PromptSpec, variables: dict[str, str]) -> str:
    """프롬프트 파일 내용으로 최종 시스템 지시를 만든다 = 템플릿 치환 결과 + 예시 블록."""
    # 템플릿을 먼저 치환하고, 예시 블록은 그 뒤에 붙인다.
    # 순서를 바꿔 통째로 치환하면 예시 안의 "$5" 같은 글자가 잘못된 자리표시로 오류를 낸다.
    return render_template(spec.system, variables) + _render_examples(spec.examples)


# ---------------------------------------------------------------------------
# 프롬프트 조립 (system + 대화 기록 + 문서 + 사용자 입력)
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
    # 문서가 없으면 아무것도 붙이지 않는다(질문 앞에 빈 줄도 생기지 않게)
    if not documents:
        return ""
    # 여는 태그부터 시작해 문서를 하나씩 덧붙인다
    parts = ["<documents>"]
    # start=1: 사람이 "1번 문서"라고 부르기 쉽게 1부터 번호를 매긴다
    for index, document in enumerate(documents, start=1):
        # 속성값은 큰따옴표로 감싸므로 " 도 이스케이프해야 속성을 위조할 수 없다.
        # escape()는 기본적으로 & < > 만 바꾸고 " 는 그대로 두기 때문에 추가 규칙으로 넘긴다.
        source = escape(document.source, {'"': "&quot;"})
        # 본문의 </documents> 같은 태그가 블록을 닫지 못하게 & < > 를 이스케이프한다.
        # 문서는 신뢰하지 않는 외부 텍스트라, 내용을 변형해서라도 블록 밖으로 못 나가게 막는다.
        parts.append(f'<document index="{index}" source="{source}">\n{escape(document.content)}\n</document>')
    # 닫는 태그
    parts.append("</documents>")
    # 각 부분을 줄바꿈으로 잇고, 뒤에 오는 질문과 빈 줄 하나로 구분한다
    return "\n".join(parts) + "\n\n"


def build_prompt(
    system: str,
    user_input: str,
    history: Sequence[Message] = (),
    documents: Sequence[Document] = (),
    max_chars: int | None = None,
) -> Prompt:
    """시스템 지시 + 대화 기록 + 문서 + 현재 입력으로 Prompt를 만든다.

    max_chars가 주어지면 system을 포함한 전체 글자 수가 그 이하가 되도록 가장 오래된 턴부터 버린다.
    현재 입력과 문서는 절대 버리지 않는다.
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
    return Prompt(system=system, messages=messages, dropped_turns=dropped, document_count=len(documents))


# ---------------------------------------------------------------------------
# 프롬프트 파일 읽기 (prompts/*.yaml)
# ---------------------------------------------------------------------------


def _check_example_text(where: str, value: object) -> None:
    """예시의 user/assistant 값 하나를 검사한다. where는 오류 메시지 앞머리(예: 'chat.yaml: examples[0].user')."""
    # 값은 문자열이어야 한다 (YAML의 no → False 같은 변환을 막는다)
    if not isinstance(value, str):
        raise TemplateError(f"{where}: must be a string, got {type(value).__name__}")
    # 우리 블록 태그의 닫는 태그가 들어 있으면 예시 블록이 중간에 닫혀 버린다.
    # 예시는 이스케이프하지 않는 대신, 이런 실수를 앱 시작 시점에 알려 준다.
    for tag in _EXAMPLE_TAGS:
        if f"</{tag}>" in value:
            raise TemplateError(f"{where}: must not contain </{tag}>")


def _parse_examples(path: str | Path, raw: list) -> tuple[Example, ...]:
    """YAML의 examples 리스트를 검증해 Example tuple로 바꾼다. 문제가 있으면 위치(examples[i])를 담아 TemplateError."""
    # 검증을 통과한 예시를 모은다
    examples = []
    for i, item in enumerate(raw):
        # 오류 메시지 앞머리. 예시가 여러 개일 때 몇 번째가 문제인지 바로 찾을 수 있게 한다.
        where = f"{path}: examples[{i}]"
        # 각 항목은 키가 정확히 user, assistant 두 개인 dict여야 한다.
        # dict의 keys()는 set과 바로 비교할 수 있어서, 이 한 줄로 누락(assistant 없음)·오타(asistant)·남는 키(note)를 모두 잡는다.
        if not isinstance(item, dict) or item.keys() != {"user", "assistant"}:
            raise TemplateError(f"{where}: must have exactly the keys 'user' and 'assistant'")
        # user, assistant 값을 각각 검사한다 (타입, 금지된 닫는 태그)
        for key in ("user", "assistant"):
            _check_example_text(f"{where}.{key}", item[key])
        # YAML의 | 블록은 끝에 줄바꿈을 남기므로 끝 공백을 지운 뒤 저장한다
        examples.append(Example(user=item["user"].rstrip(), assistant=item["assistant"].rstrip()))
    # 불변 tuple로 돌려준다
    return tuple(examples)


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
    missing = _REQUIRED_FIELDS.keys() - data.keys()
    if missing:
        raise TemplateError(f"{path}: missing required keys: {', '.join(sorted(missing))}")
    # 필수도 선택도 아닌 키 (sytem 같은 오타). YAML은 오타 키를 조용히 받아들이므로 직접 막는다.
    # 바다코끼리 연산자(:=)로 계산과 동시에 변수에 담고, 비어 있지 않으면 오류.
    if unknown := data.keys() - _REQUIRED_FIELDS.keys() - _OPTIONAL_FIELDS.keys():
        # YAML 키는 문자열이 아닐 수 있다(1: x → int 키). map(str)로 바꿔야 sorted/join이 TypeError를 내지 않는다.
        raise TemplateError(f"{path}: unknown keys: {', '.join(sorted(map(str, unknown)))}")

    # 각 키의 값 타입 검사. YAML 1.1에서는 no → False, 1.0 → float처럼 의도와 다른 타입이 될 수 있다.
    # | 는 두 dict를 합친다(Python 3.9+).
    for key, kind in (_REQUIRED_FIELDS | _OPTIONAL_FIELDS).items():
        # 선택 키는 없을 수 있으므로 "있는 키만" 검사한다. key in data 가 빠지면 examples 없는 파일이 KeyError로 죽는다.
        if key in data and not isinstance(data[key], kind):
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
        # examples가 없는 파일이면 빈 리스트로 검증 → 빈 tuple
        examples=_parse_examples(path, data.get("examples", [])),
    )

"""프롬프트 구성기 EDD 레벨 1 평가 실행기 (모델 호출 없음, 비용 0, 결정적).

실제 운영 프롬프트(prompts/chat.yaml)에 시나리오별 입력(사용자 입력, 대화 기록, 문서, 예산)을 넣어
최종 입력을 조립하고, 두 종류의 기준으로 채점한다.

1. 불변식 검사 (모든 성공 케이스에 자동 적용. 라벨이 필요 없고 명세에서 나온다)
    system_unchanged     시스템 지시가 입력과 무관하게 렌더링 결과 그대로인가 (사용자 입력·문서가 system에 섞이지 않음)
    structure            user로 시작해 user/assistant가 번갈아 나오고 user로 끝나는가
    current_input_last   마지막 메시지가 이번 사용자 입력으로 끝나는가 (입력이 잘리거나 바뀌지 않음)
    within_budget        max_chars가 있으면 전체 크기가 예산 이하인가
    documents_contained  문서 블록을 XML로 파싱했을 때 문서 수·순서·출처·내용이 원본과 같은가
                         (문서 안의 닫는 태그나 따옴표가 구조를 깨뜨리지 못함 = 주입 차단)
    minimal_drop         버린 턴이 가장 오래된 것부터이고, 하나라도 덜 버리면 예산을 넘는가
2. 라벨 검사 (사람이 케이스마다 단 기대값)
    outcome              "ok" 또는 "error"(+ error 클래스 이름)
    dropped_turns        버려야 하는 턴 수

사용법:
    python evals/run_prompt_eval.py              # evals/prompt_cases.jsonl
    python evals/run_prompt_eval.py -v           # PASS 케이스도 표시
    python evals/run_prompt_eval.py --id <id>    # 케이스 하나의 조립 결과를 자세히
종료 코드: 게이트 통과 0, 실패 1

레벨 2(나중): 같은 케이스로 조립한 프롬프트를 실제 모델에 보내 응답을 채점한다(비용 발생).
케이스 파일에 레벨 2용 필드를 추가해도 이 실행기는 모르는 필드를 무시한다.
"""

import argparse
import os
import sys
import xml.etree.ElementTree as ET
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)

import caseio  # noqa: E402
from chatbot.prompt_builder import (  # noqa: E402
    Document,
    Message,
    build_prompt,
    load_prompt_spec,
    render_system,
    spec_version,
)

DEFAULT_FILE = os.path.join(HERE, "prompt_cases.jsonl")
PROMPT_FILE = os.path.join(ROOT, "prompts", "chat.yaml")

CHECKS = (
    "system_unchanged",
    "structure",
    "current_input_last",
    "within_budget",
    "documents_contained",
    "minimal_drop",
    "outcome",
    "dropped_turns",
)


def build_args(case: dict) -> dict:
    """케이스의 input을 build_prompt 인자로 바꾼다. history는 [role, content] 목록(일부러 잘못된 순서도 쓸 수 있게)."""
    inp = case["input"]
    return {
        "user_input": inp["user_input"],
        "history": [Message(role=r, content=c) for r, c in inp.get("history", [])],
        "documents": [Document(source=s, content=c) for s, c in inp.get("documents", [])],
        "max_chars": inp.get("max_chars"),
    }


def check_documents(content: str, user_input: str, documents: list) -> bool:
    """문서 블록이 원본 문서를 그대로, 구조를 깨지 않고 담았는지 XML 파싱으로 확인한다."""
    head = content[: len(content) - len(user_input)]
    if not documents:
        return head == ""
    if not head.endswith("\n\n"):
        return False
    try:
        root = ET.fromstring(head)
    except ET.ParseError:
        return False
    items = list(root)
    if root.tag != "documents" or len(items) != len(documents):
        return False
    for index, (item, doc) in enumerate(zip(items, documents), start=1):
        if item.tag != "document" or item.get("index") != str(index) or item.get("source") != doc.source:
            return False
        if item.text != f"\n{doc.content}\n" or len(item):
            return False
    return True


def check_minimal_drop(prompt, history: list, max_chars) -> bool:
    """남은 기록이 원래 기록의 뒤쪽 부분이고, 버린 턴 중 가장 최근 것 하나를 되살리면 예산을 넘는지 확인한다."""
    kept = list(prompt.messages[:-1])
    dropped = prompt.dropped_turns
    if kept != history[2 * dropped :]:
        return False
    if dropped == 0:
        return True
    restored = history[2 * (dropped - 1) : 2 * dropped]
    return prompt.metadata()["total_chars"] + sum(len(m.content) for m in restored) > max_chars


def evaluate(case: dict, system: str) -> tuple[str, list[str], object]:
    """(상태, 실패한 검사 이름들, 조립 결과 또는 예외)를 돌려준다."""
    expect = case["expect"]
    args = build_args(case)
    try:
        prompt = build_prompt(system=system, **args)
    except ValueError as e:
        if expect["outcome"] == "error" and type(e).__name__ == expect.get("error"):
            return "PASS", [], e
        return "FAIL", ["outcome"], e
    except Exception as e:  # 명세에 없는 예외 = 구성기 버그
        return "ERROR", [], e

    if expect["outcome"] != "ok":
        return "FAIL", ["outcome"], prompt

    roles = [m.role for m in prompt.messages]
    results = {
        "system_unchanged": prompt.system == system,
        "structure": roles == ["user", "assistant"] * (len(roles) // 2) + ["user"],
        "current_input_last": prompt.messages[-1].content.endswith(args["user_input"]),
        "within_budget": args["max_chars"] is None or prompt.metadata()["total_chars"] <= args["max_chars"],
        "documents_contained": check_documents(prompt.messages[-1].content, args["user_input"], args["documents"]),
        "minimal_drop": check_minimal_drop(prompt, args["history"], args["max_chars"]),
        "dropped_turns": prompt.dropped_turns == expect.get("dropped_turns", 0),
    }
    failed = [name for name, ok in results.items() if not ok]
    return ("FAIL" if failed else "PASS"), failed, prompt


def validate(case: dict) -> list[str]:
    problems = []
    if "id" not in case:
        problems.append("id 없음")
    if not isinstance(case.get("input"), dict) or "user_input" not in case["input"]:
        problems.append("input.user_input 없음")
    expect = case.get("expect")
    if not isinstance(expect, dict) or expect.get("outcome") not in ("ok", "error"):
        problems.append("expect.outcome은 ok/error 중 하나여야 함")
    elif expect["outcome"] == "error" and "error" not in expect:
        problems.append("error 케이스는 expect.error(예외 클래스 이름)가 필요")
    return problems


def show_case(case: dict, system: str) -> None:
    status, failed, result = evaluate(case, system)
    print(f"[{status}] {case['id']}  {case.get('note', '')}")
    if failed:
        print(f"  실패한 검사: {', '.join(failed)}")
    if isinstance(result, Exception):
        print(f"  예외: {type(result).__name__}: {result}")
        return
    print(f"  metadata: {result.metadata()}")
    print(f"  --- system ---\n{result.system}")
    for m in result.messages:
        print(f"  --- {m.role} ---\n{m.content}")


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")  # Windows 콘솔/파이프의 cp949에서 한글이 깨지지 않게
    parser = argparse.ArgumentParser(description="프롬프트 구성기 EDD 레벨 1 평가")
    parser.add_argument("file", nargs="?", default=DEFAULT_FILE)
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("--id")
    args = parser.parse_args()

    spec = load_prompt_spec(PROMPT_FILE)
    system = render_system(spec, {})
    cases = caseio.load_cases(args.file)

    if args.id:
        matches = [c for c in cases if c.get("id") == args.id]
        if not matches:
            print(f"케이스 없음: {args.id}")
            return 1
        show_case(matches[0], system)
        return 0

    print(f"프롬프트: {spec.id} (prompt_version {spec_version(spec)})  케이스: {len(cases)}개  파일: {args.file}")
    statuses, check_fails, tag_fails = Counter(), Counter(), Counter()
    for case in cases:
        problems = validate(case)
        if problems:
            status, failed, result = "ERROR", [], ValueError("; ".join(problems))
        else:
            status, failed, result = evaluate(case, system)
        statuses[status] += 1
        check_fails.update(failed)
        if status != "PASS":
            tag_fails.update(case.get("tags", []))
        if status != "PASS" or args.verbose:
            detail = f"  실패: {', '.join(failed)}" if failed else ""
            if isinstance(result, Exception) and status != "PASS":
                detail += f"  ({type(result).__name__}: {result})"
            print(f"  [{status}] {case.get('id', case['_where'])}{detail}")

    print()
    print(f"결과: PASS {statuses['PASS']} / FAIL {statuses['FAIL']} / ERROR {statuses['ERROR']}")
    print("검사별 실패: " + ", ".join(f"{name} {check_fails[name]}" for name in CHECKS))
    if tag_fails:
        print("실패 케이스의 태그: " + ", ".join(f"{t} {n}" for t, n in tag_fails.most_common()))

    # 게이트: 레벨 1은 결정적 검사라 실패 허용치가 없다
    passed = statuses["FAIL"] == 0 and statuses["ERROR"] == 0
    print("GATE PASSED" if passed else "GATE FAILED")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())

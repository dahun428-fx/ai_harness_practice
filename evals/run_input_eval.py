"""입력처리기 EDD 평가 실행기.

eval 케이스를 process()에 넣고, 사람이 단 라벨과 비교해 케이스별 결과와 지표를 보여 준다.

사용법:
    python evals/run_input_eval.py                         # input_cases + random_cases
    python evals/run_input_eval.py evals/input_cases.jsonl # 특정 파일만
    python evals/run_input_eval.py -v                      # PASS 케이스도 한 줄씩 표시
    python evals/run_input_eval.py --tag emoji             # 태그로 거르기
    python evals/run_input_eval.py --id rand-42-007        # 케이스 하나만 자세히

케이스 상태:
    PASS    라벨과 일치
    FAIL    라벨과 불일치 → 게이트에 반영
    REVIEW  불일치지만 "open-question" 태그가 붙은 케이스 → 사람이 판단할 거리, 게이트 제외
    ERROR   process()가 예외를 던짐 (명세 위반) 또는 케이스 형식 오류 → 게이트에 반영

실행할 때마다 evals/results/run-YYYYmmdd-HHMMSS.jsonl 에 케이스별 결과와 요약을 남기고,
직전 실행과 비교해 상태가 바뀐 케이스를 알려 준다.
종료 코드: 게이트 통과 0, 실패 1
"""

import argparse
import glob
import json
import os
import sys
import time
import traceback
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

import caseio  # noqa: E402
from chatbot.input_processor import process  # noqa: E402

DEFAULT_FILES = ["input_cases.jsonl", "random_cases.jsonl"]
RESULTS_DIR = os.path.join(HERE, "results")

# 게이트(기준선). EDD에서 "테스트 통과"에 해당한다. 필요하면 여기서 조정.
GATE = {
    "false_accept": 0,          # 거부해야 할 입력을 통과시킨 수
    "wrong_error": 0,           # 거부는 했지만 오류 코드가 틀린 수
    "wrong_normalization": 0,   # 통과는 했지만 정규화 결과가 틀린 수
    "error": 0,                 # process()가 예외를 던진 수
    "false_reject_rate": 0.01,  # 정상 입력을 거부한 비율 (정상 입력 수 대비)
}

KIND_LABEL = {
    "false_reject": "오거부 (통과해야 하는데 거부)",
    "false_accept": "오통과 (거부해야 하는데 통과)",
    "wrong_error": "오류 코드 불일치",
    "wrong_normalization": "정규화 결과 불일치",
    "error": "예외 발생",
    "invalid_case": "케이스 형식 오류",
}

USE_COLOR = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None
COLORS = {"PASS": "32", "FAIL": "31", "REVIEW": "33", "ERROR": "35"}


def paint(status: str, text: str | None = None) -> str:
    text = text if text is not None else status
    return f"\033[{COLORS[status]}m{text}\033[0m" if USE_COLOR else text


def rel(path: str) -> str:
    try:
        return os.path.relpath(path)
    except ValueError:  # Windows에서 드라이브가 다르면 상대 경로를 만들 수 없음
        return path


def show(value, limit: int = 70) -> str:
    """보이지 않는 문자가 드러나도록 repr로, 너무 길면 가운데를 줄여서 보여 준다."""
    r = repr(value)
    if len(r) > limit:
        r = f"{r[: limit // 2]} … {r[-limit // 2 :]}"
    if isinstance(value, (str, bytes)):
        r += f"  (len={len(value)})"
    return r


def judge(case: dict) -> dict:
    record = {"id": case.get("id"), "tags": case.get("tags", []), "source": case.get("source"),
              "where": case["_where"], "note": case.get("note")}

    problems = caseio.validate_case(case)
    if problems:
        return {**record, "status": "ERROR", "kind": "invalid_case", "detail": "; ".join(problems)}

    raw = caseio.build_input(case)
    expect = case["expect"]
    record["input"] = raw
    record["expected"] = (
        {"ok": True, "text": caseio.expected_text(case)} if expect == "accept" else {"ok": False, "error": case["error"]}
    )

    try:
        result = process(raw)
    except Exception:
        return {**record, "status": "ERROR", "kind": "error", "detail": traceback.format_exc(limit=3)}

    record["actual"] = {"ok": result.ok, "text": result.text, "error": result.error.name if result.error else None}

    if expect == "accept":
        if not result.ok:
            kind = "false_reject"
        elif result.text != caseio.expected_text(case):
            kind = "wrong_normalization"
        else:
            kind = None
    else:
        if result.ok:
            kind = "false_accept"
        elif result.error.name != case["error"]:
            kind = "wrong_error"
        else:
            kind = None

    if kind is None:
        status = "PASS"
    elif "open-question" in record["tags"]:
        status = "REVIEW"
    else:
        status = "FAIL"
    return {**record, "status": status, "kind": kind}


def describe(rec: dict) -> str:
    def outcome(d):
        return f"accept {show(d['text'])}" if d["ok"] else f"reject {d['error']}"

    lines = [f"{paint(rec['status'], rec['status'].ljust(6))} {rec['id']}   [{', '.join(rec['tags'])}]",
             f"       종류     : {KIND_LABEL.get(rec['kind'], rec['kind'])}",
             f"       위치     : {rec['where']}"]
    if "input" in rec:
        lines.append(f"       입력     : {show(rec['input'])}")
    if "expected" in rec:
        lines.append(f"       기대     : {outcome(rec['expected'])}")
    if "actual" in rec:
        lines.append(f"       실제     : {outcome(rec['actual'])}")
    if rec.get("detail"):
        lines.append("       상세     : " + rec["detail"].strip().replace("\n", "\n                  "))
    if rec.get("note"):
        lines.append(f"       메모     : {rec['note']}")
    return "\n".join(lines)


def summarize(records: list[dict]) -> dict:
    statuses = Counter(r["status"] for r in records)
    gated = [r for r in records if r["status"] != "REVIEW"]
    kinds = Counter(r["kind"] for r in gated if r["kind"])
    expect_accept = sum(1 for r in gated if r.get("expected", {}).get("ok") is True)
    metrics = {
        "false_accept": kinds["false_accept"],
        "wrong_error": kinds["wrong_error"],
        "wrong_normalization": kinds["wrong_normalization"],
        "error": kinds["error"] + kinds["invalid_case"],
        "false_reject": kinds["false_reject"],
        "false_reject_rate": kinds["false_reject"] / expect_accept if expect_accept else 0.0,
    }
    gate = {name: metrics[name] <= limit for name, limit in GATE.items()}
    return {"total": len(records), "statuses": dict(statuses), "metrics": metrics,
            "gate": gate, "gate_passed": all(gate.values())}


def tag_table(records: list[dict]) -> list[str]:
    by_tag = defaultdict(Counter)
    for r in records:
        for t in r["tags"] or ["(없음)"]:
            by_tag[t][r["status"]] += 1
    rows = [f"  {'태그':<22}{'전체':>5}{'PASS':>6}{'FAIL':>6}{'REVIEW':>8}{'ERROR':>7}   정확도"]
    for tag, c in sorted(by_tag.items(), key=lambda kv: (-(kv[1]['FAIL'] + kv[1]['ERROR']), kv[0])):
        total = sum(c.values())
        rows.append(f"  {tag:<22}{total:>5}{c['PASS']:>6}{c['FAIL']:>6}{c['REVIEW']:>8}{c['ERROR']:>7}"
                    f"   {c['PASS'] / total:6.1%}")
    return rows


def previous_statuses() -> tuple[str | None, dict]:
    runs = sorted(glob.glob(os.path.join(RESULTS_DIR, "run-*.jsonl")))
    if not runs:
        return None, {}
    statuses = {}
    with open(runs[-1], encoding="utf-8") as f:
        for line in f:
            row = json.loads(line)
            if "id" in row:
                statuses[row["id"]] = row["status"]
    return runs[-1], statuses


def save_log(records: list[dict], summary: dict, files: list[str]) -> str:
    os.makedirs(RESULTS_DIR, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S") + f"-{int(time.time() * 1000) % 1000:03d}"
    path = os.path.join(RESULTS_DIR, f"run-{stamp}.jsonl")
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        for r in records:
            row = {k: v for k, v in r.items() if k not in ("input",)}
            if isinstance(r.get("input"), bytes):
                row["input_bytes_hex"] = r["input"].hex()
            elif "input" in r:
                row["input"] = r["input"]
            f.write(caseio.dumps(row) + "\n")
        f.write(caseio.dumps({"summary": summary, "files": files, "gate_limits": GATE}) + "\n")
    return path


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("files", nargs="*")
    parser.add_argument("-v", "--verbose", action="store_true", help="PASS 케이스도 표시")
    parser.add_argument("--tag", help="이 태그가 붙은 케이스만")
    parser.add_argument("--id", help="이 ID의 케이스만 (자세히)")
    parser.add_argument("--no-log", action="store_true", help="results/ 에 기록하지 않음")
    args = parser.parse_args()

    files = args.files or [os.path.join(HERE, f) for f in DEFAULT_FILES if os.path.exists(os.path.join(HERE, f))]
    cases = [c for path in files for c in caseio.load_cases(path)]
    if args.tag:
        cases = [c for c in cases if args.tag in c.get("tags", [])]
    if args.id:
        cases = [c for c in cases if c.get("id") == args.id]
    if not cases:
        print("실행할 케이스가 없습니다.")
        return 1

    dup = [i for i, n in Counter(c.get("id") for c in cases).items() if n > 1]
    if dup:
        print(f"중복 ID: {dup}")
        return 1

    records = [judge(c) for c in cases]

    print(f"\n입력처리기 eval — {len(records)}개 케이스 ({', '.join(rel(f) for f in files)})\n")
    for rec in records:
        if rec["status"] == "PASS" and not (args.verbose or args.id):
            continue
        if rec["status"] == "PASS":
            print(f"{paint('PASS', 'PASS  ')} {rec['id']}   [{', '.join(rec['tags'])}]")
        else:
            print(describe(rec) + "\n")

    summary = summarize(records)
    m, s = summary["metrics"], summary["statuses"]
    print("─" * 72)
    print("  " + "   ".join(f"{paint(k, k)} {s.get(k, 0)}" for k in ("PASS", "FAIL", "REVIEW", "ERROR")))
    print()
    print("지표 (REVIEW 제외)")
    print(f"  오거부율 (false reject)     {m['false_reject_rate']:6.1%}  ({m['false_reject']}건)   기준 ≤ {GATE['false_reject_rate']:.0%}")
    print(f"  오통과 (false accept)       {m['false_accept']:>6}건                 기준 ≤ {GATE['false_accept']}")
    print(f"  오류 코드 불일치            {m['wrong_error']:>6}건                 기준 ≤ {GATE['wrong_error']}")
    print(f"  정규화 결과 불일치          {m['wrong_normalization']:>6}건                 기준 ≤ {GATE['wrong_normalization']}")
    print(f"  예외·형식 오류              {m['error']:>6}건                 기준 ≤ {GATE['error']}")
    print()
    print("태그별")
    print("\n".join(tag_table(records)))
    print()

    if not args.no_log and not args.id:
        prev_path, prev = previous_statuses()
        changed = [(r["id"], prev[r["id"]], r["status"]) for r in records if r["id"] in prev and prev[r["id"]] != r["status"]]
        if prev_path:
            print(f"직전 실행 대비 ({rel(prev_path)})")
            if changed:
                for cid, before, after in changed:
                    print(f"  {cid}: {paint(before, before)} → {paint(after, after)}")
            else:
                print("  상태가 바뀐 케이스 없음")
            print()
        log_path = save_log(records, summary, files)
        print(f"기록: {rel(log_path)}")

    failed = [name for name, ok in summary["gate"].items() if not ok]
    if summary["gate_passed"]:
        print(paint("PASS", "GATE PASSED"))
    else:
        print(paint("FAIL", "GATE FAILED") + f" — 기준 초과: {', '.join(failed)}")
    return 0 if summary["gate_passed"] else 1


if __name__ == "__main__":
    sys.exit(main())

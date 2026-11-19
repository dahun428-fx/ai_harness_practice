"""트레이스(traces/*.jsonl)에서 eval 후보 케이스를 뽑는다.

하네스 루프의 연결 고리:  실사용 → traces/ → (이 스크립트) → evals/candidates.jsonl → 사람이 검토 → input_cases.jsonl

후보에는 지금 구현이 낸 결과를 expect/normalized로 미리 채워 두지만, 그건 제안일 뿐이다.
사람이 보고 "이게 맞는 동작인가"를 판단해 고친 뒤 input_cases.jsonl로 옮긴다.
(라벨을 process() 결과로 확정하면 eval이 아무것도 검증하지 못한다.)

사용법:
    python evals/traces_to_cases.py                 # 거부됐거나 정규화로 바뀐 입력만
    python evals/traces_to_cases.py --all           # 모든 입력
    python evals/traces_to_cases.py --since 2026-11-01
"""

import argparse
import glob
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)

import caseio  # noqa: E402


def iter_turns(trace_dir: str, since: str | None):
    for path in sorted(glob.glob(os.path.join(trace_dir, "*.jsonl"))):
        if since and os.path.basename(path)[:10] < since:
            continue
        with open(path, encoding="utf-8") as f:
            for line in f:
                row = json.loads(line)
                if row.get("type") == "turn":
                    yield row


def raw_input_of(turn: dict):
    raw = turn["input"]["raw"]
    if isinstance(raw, dict) and "bytes_hex" in raw:
        return {"input_bytes_hex": raw["bytes_hex"]}
    return {"input": raw}


def to_candidate(turn: dict) -> dict:
    observed = turn["input"]
    case = {"id": f"trace-{turn['session_id']}-{turn['turn']:03d}", **raw_input_of(turn)}
    if observed["ok"]:
        case.update(expect="accept", normalized=observed["normalized"])
    else:
        case.update(expect="reject", error=observed["error"])
    changed = observed["ok"] and observed["normalized"] != observed["raw"]
    case["tags"] = ["from-trace", "rejected" if not observed["ok"] else "changed" if changed else "unchanged"]
    case["source"] = "real"
    case["label_status"] = "needs-review"
    case["note"] = f"traced {turn.get('time', '?')} — expect/normalized는 현재 구현의 결과(제안). 검토 후 확정할 것"
    return case


def known_inputs(paths: list[str]) -> set[str]:
    seen = set()
    for path in paths:
        if os.path.exists(path):
            for case in caseio.load_cases(path):
                seen.add(json.dumps({k: case.get(k) for k in caseio.INPUT_KEYS}, sort_keys=True))
    return seen


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--traces", default=os.path.join(ROOT, "traces"))
    parser.add_argument("--out", default=os.path.join(HERE, "candidates.jsonl"))
    parser.add_argument("--all", action="store_true", help="정규화로 바뀌지 않은 정상 입력도 포함")
    parser.add_argument("--since", help="YYYY-MM-DD 이후 트레이스만")
    args = parser.parse_args()

    seen = known_inputs([os.path.join(HERE, "input_cases.jsonl"), args.out])
    new = []
    for turn in iter_turns(args.traces, args.since):
        case = to_candidate(turn)
        if not args.all and "unchanged" in case["tags"]:
            continue
        key = json.dumps({k: case.get(k) for k in caseio.INPUT_KEYS}, sort_keys=True)
        if key in seen:
            continue  # 이미 eval에 있거나 이미 후보로 뽑힌 입력
        seen.add(key)
        new.append(case)

    with open(args.out, "a", encoding="utf-8", newline="\n") as f:
        for case in new:
            f.write(caseio.dumps(case) + "\n")
    print(f"후보 {len(new)}건 추가 → {args.out}")
    for case in new:
        print(f"  {case['id']}  [{', '.join(case['tags'])}]  {case['expect']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

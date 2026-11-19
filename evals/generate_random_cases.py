"""무작위 eval 케이스 생성기.

깨끗한 기준 문장(base)에 "결과를 알고 있는 노이즈"를 무작위로 섞어 입력을 만든다.
기대값은 process()를 돌려서 얻지 않는다. 노이즈를 어떻게 섞었는지로부터 사람이 정한
규칙(명세)에 따라 계산한다. 예: base에 ZWSP를 끼워 넣었으면 기대값은 base 그대로.

사용법:
    python evals/generate_random_cases.py                    # seed=42, 60개
    python evals/generate_random_cases.py --seed 7 --count 200
같은 seed는 항상 같은 케이스를 만든다 (실패를 재현할 수 있어야 하므로).
"""

import argparse
import os
import random
import sys
import unicodedata

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import caseio  # noqa: E402

MAX_LENGTH = 4000  # 명세 값. 구현에서 import하지 않는다 (구현이 틀려도 기대값은 명세를 따라야 함)

# 이미 정규화된 상태의 문장들: NFC, LF 줄바꿈, 앞뒤 공백 없음, 보이지 않는 문자 없음
BASES = [
    "안녕하세요",
    "오늘 서울 날씨 어때?",
    "파이썬 리스트 컴프리헨션 예시 보여줘",
    "How do I center a div?",
    "React에서 상태 관리 뭐 써?",
    "def add(a, b):\n\treturn a + b",
    "첫 문단\n\n둘째 문단",
    "\U0001F468‍\U0001F469‍\U0001F467 가족 여행 추천",
    "고마워❤️",
    "\U0001F1F0\U0001F1F7 대한민국",
    "가격은 ₩12,000이야",
    "① 첫째 ② 둘째",
    "맥북에서 한글 파일명이 깨져",
]

EDGE_WHITESPACE = [" ", "\t", "\n", "　", " "]
INVISIBLE = ["​", "﻿", "\x00", "\x01", "\x07", "\x1b", "\x7f"]
NON_TEXT = [None, 0, 3.14, True, [], {}, ["안녕"]]


# ---- accept 케이스용 노이즈: 적용해도 기대값(base)은 바뀌지 않아야 한다 ----

def pad_edges(rng, s):
    pick = lambda: "".join(rng.choice(EDGE_WHITESPACE) for _ in range(rng.randint(1, 4)))  # noqa: E731
    return pick() + s + pick()


def insert_invisible(rng, s):
    chars = list(s)
    for _ in range(rng.randint(1, 4)):
        chars.insert(rng.randint(0, len(chars)), rng.choice(INVISIBLE))
    return "".join(chars)


def to_crlf(rng, s):
    return s.replace("\n", "\r\n")


def to_cr(rng, s):
    return s.replace("\n", "\r")


def to_nfd(rng, s):
    return unicodedata.normalize("NFD", s)


NOISES = {
    "pad-edges": pad_edges,
    "insert-invisible": insert_invisible,
    "crlf": to_crlf,
    "cr": to_cr,
    "nfd": to_nfd,
}


def make_accept(rng, i, seed):
    if rng.random() < 0.15:
        base = "가" * rng.randint(MAX_LENGTH - 10, MAX_LENGTH)  # 길이 경계 근처
        tags = ["length-boundary"]
    else:
        base = rng.choice(BASES)
        tags = []
    names = rng.sample(sorted(NOISES), k=rng.randint(1, 3))
    if "crlf" in names and "cr" in names:
        names.remove("cr")
    # 노이즈 적용 순서도 무작위. 순서에 따라 결과가 달라지면 그게 발견이다.
    raw = base
    for name in names:
        raw = NOISES[name](rng, raw)
    case = {"id": f"rand-{seed}-{i:03d}", "input": raw, "expect": "accept", "normalized": base}
    return case, tags + names


# ---- reject 케이스 ----

def make_reject(rng, i, seed):
    kind = rng.choice(["empty", "too-long", "truncated-utf8", "lone-surrogate", "non-text"])
    case = {"id": f"rand-{seed}-{i:03d}", "expect": "reject"}
    if kind == "empty":
        pool = EDGE_WHITESPACE + INVISIBLE
        case["input"] = "".join(rng.choice(pool) for _ in range(rng.randint(1, 10)))
        case["error"] = "EMPTY"
    elif kind == "too-long":
        base = "가" * rng.randint(MAX_LENGTH + 1, MAX_LENGTH + 10)
        case["input"] = pad_edges(rng, insert_invisible(rng, base))
        case["error"] = "TOO_LONG"
    elif kind == "truncated-utf8":
        data = rng.choice([b for b in BASES if not b.isascii()]).encode("utf-8")
        # 멀티바이트 문자 하나를 골라 그 문자의 중간에서 자른다 → 끝이 불완전한 UTF-8
        start = rng.choice([j for j, byte in enumerate(data) if byte >= 0xC0])
        width = 2 if data[start] < 0xE0 else 3 if data[start] < 0xF0 else 4
        case["input_bytes_hex"] = data[: rng.randint(start + 1, start + width - 1)].hex()
        case["error"] = "INVALID_ENCODING"
    elif kind == "lone-surrogate":
        s = list(rng.choice(BASES))
        s.insert(rng.randint(0, len(s)), chr(rng.randint(0xD800, 0xDFFF)))
        case["input"] = "".join(s)
        case["error"] = "INVALID_ENCODING"
    else:
        case["input"] = rng.choice(NON_TEXT)
        case["error"] = "INVALID_TYPE"
    return case, [kind]


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--count", type=int, default=60)
    parser.add_argument("--reject-ratio", type=float, default=0.35)
    parser.add_argument("--out", default=os.path.join(os.path.dirname(__file__), "random_cases.jsonl"))
    args = parser.parse_args()

    rng = random.Random(args.seed)
    with open(args.out, "w", encoding="utf-8", newline="\n") as f:
        for i in range(args.count):
            make = make_reject if rng.random() < args.reject_ratio else make_accept
            case, tags = make(rng, i, args.seed)
            case["tags"] = ["generated", *tags]
            case["source"] = "generated"
            assert not caseio.validate_case(case), caseio.validate_case(case)
            f.write(caseio.dumps(case) + "\n")
    print(f"{args.count}개 케이스 생성 (seed={args.seed}) → {args.out}")


if __name__ == "__main__":
    main()

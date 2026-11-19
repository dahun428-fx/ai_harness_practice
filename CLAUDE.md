# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Practice project: a terminal chatbot on the Claude Agent SDK (`claude-agent-sdk`), built TDD + EDD (eval-driven development). The finished piece so far is the **input processor** — validation and normalization of user input before it reaches Claude. Its spec is `docs/input-processor.md`; read it before changing `chatbot/input_processor.py`.

## Commands

```bash
source .venv/Scripts/activate          # Git Bash (PowerShell: .\.venv\Scripts\Activate.ps1)
pip install -r requirements-dev.txt    # runtime + pytest + hypothesis

pytest                                  # all tests
pytest tests/test_input_processor.py -k nfc   # single test by name
python evals/run_input_eval.py          # eval gate; exit 1 on failure; logs to evals/results/
python evals/run_input_eval.py --id <case-id>        # inspect one eval case
python evals/generate_random_cases.py --seed 7 --count 300 --out evals/tmp.jsonl   # fuzz cases (don't commit)
python main.py                          # run the chatbot (LOG_LEVEL=DEBUG for chatbot.* debug logs)
```

CI (`.github/workflows/ci.yml`) runs pytest, the eval gate, and 5 random seeds × 300 cases on every push/PR.

## Architecture

- `chatbot/input_processor.py` — pure function `process(raw) -> ProcessResult`; never raises. Stages: type check → UTF-8 decode → encodability check → `_normalize` → empty check → length check. Every rejection goes through one `except _Rejected` block (single logging point).
- **Order inside `_normalize` is load-bearing**: invisible-char removal must run before CSI/OSC stripping, CRLF folding, and NFC — eval fuzzing found real bugs when it didn't. ESC, BEL, `\r`, NEL are deliberately kept by the first step because later steps need them.
- `chatbot/messages.py` — user-facing text per `ErrorCode`; a parametrized test forces a message for every enum member.
- `chatbot/logging_config.py` — `JsonFormatter` (JSON Lines, `extra` fields included, tracebacks under `exception`) and `setup_logging()`, which attaches a handler to the root logger directly (`basicConfig` silently no-ops under pytest).
- `main.py` — I/O only: stdin/stdout UTF-8, loop, SDK calls. Guarded by `if __name__ == "__main__"` so tests can import modules without starting the bot.
- `evals/` — `caseio.py` (JSONL format, escapes invisible chars), `run_input_eval.py` (PASS/FAIL/REVIEW/ERROR per case, metrics, gate, run-to-run diff), `generate_random_cases.py` (clean base text + noise with known effect; labels come from the spec, never from `process()`).

## Rules

- Never log raw user input or Claude output at any level; tests (`*_never_logged`) enforce it.
- Eval labels are human/spec decisions. When the spec changes, update the generator's noise assumptions too (e.g. ESC/BEL were removed from "neutral" noise once they gained meaning).
- Write invisible characters in source/tests/docs as `\uXXXX` escapes, never raw.
- The user is learning TDD/EDD: by default give step-by-step guidance and verify their work ("확인해"); edit files directly only when they hand the task over.

## Environment

- Windows, Python 3.12, venv at `.venv/`. User's terminal is often Git Bash (MINGW64): give POSIX-style commands.
- Git Bash pipes stdin as cp949 unless reconfigured — `main.py` forces UTF-8; reproduce issues with `printf '안녕\nexit\n' | python main.py`.

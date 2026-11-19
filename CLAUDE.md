# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project status

Early-stage Python practice project for AI harness experiments. No source code, dependencies, or tests exist yet — update this file once structure emerges.

## Environment

- Python 3.12, virtualenv at `.venv/` (git-ignored).
- The user's terminal is often Git Bash (MINGW64) on Windows, so give POSIX-style commands:
  - Activate: `source .venv/Scripts/activate` (PowerShell: `.\.venv\Scripts\Activate.ps1`)
  - Install: `pip install <pkg>`, then `pip freeze > requirements.txt`
  - Recreate: `py -3.12 -m venv .venv && source .venv/Scripts/activate && pip install -r requirements.txt`

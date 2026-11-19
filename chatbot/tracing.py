"""대화 트레이스: 품질 평가와 eval 데이터 수집을 위한 원문 기록.

운영 로그(logging, logs/)와는 의도적으로 분리한다.
- 운영 로그: 메타데이터만, 항상 켜짐, 원문 금지
- 트레이스: 입력·응답 원문 포함, traces/ 에만 저장, TRACE=0 으로 끌 수 있음, 보관 기간이 지나면 삭제

트레이스는 logging 모듈을 거치지 않는다. 원문이 운영 로그 핸들러로 새어 나가지 않게 하기 위해서다.
"""

import hashlib
import subprocess
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from chatbot.jsonl import dumps


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class TraceWriter:
    def __init__(self, directory, enabled: bool = True, now: Callable[[], datetime] = _utc_now):
        self.directory = Path(directory)
        self.enabled = enabled
        self._now = now

    def write(self, record: dict) -> None:
        if not self.enabled:
            return
        now = self._now()
        # 보이지 않는 문자와 lone surrogate(깨진 입력)는 이스케이프로 남긴다:
        # 라벨 다는 사람이 볼 수 있고, surrogate도 UTF-8 파일에 쓸 수 있다
        line = dumps({"time": now.isoformat(), **_bytes_to_hex(record)}, default=str)
        self.directory.mkdir(parents=True, exist_ok=True)
        with open(self.directory / f"{now:%Y-%m-%d}.jsonl", "a", encoding="utf-8") as f:
            f.write(line + "\n")

    def cleanup(self, retention_days: int) -> list[Path]:
        if not self.directory.exists():
            return []
        cutoff = (self._now() - timedelta(days=retention_days)).date()
        deleted = []
        for path in self.directory.glob("*.jsonl"):
            try:
                day = datetime.strptime(path.stem, "%Y-%m-%d").date()
            except ValueError:
                continue  # 트레이스 파일이 아닌 것은 건드리지 않는다
            if day < cutoff:
                path.unlink()
                deleted.append(path)
        return deleted


def _bytes_to_hex(record: dict) -> dict:
    """bytes 값(중첩 포함)을 {"bytes_hex": ...}로 바꾼다. JSON에는 bytes를 넣을 수 없다."""

    def convert(value):
        if isinstance(value, bytes):
            return {"bytes_hex": value.hex()}
        if isinstance(value, dict):
            return {k: convert(v) for k, v in value.items()}
        if isinstance(value, list):
            return [convert(v) for v in value]
        return value

    return {k: convert(v) for k, v in record.items()}


def new_session_id() -> str:
    return uuid.uuid4().hex[:12]


def prompt_version(system_prompt: str) -> str:
    """시스템 프롬프트가 바뀌었는지 추적하기 위한 짧은 해시."""
    return hashlib.sha256(system_prompt.encode("utf-8")).hexdigest()[:12]


def app_version(repo_dir) -> dict:
    """트레이스가 어떤 코드로 만들어졌는지. git이 없으면 None."""

    def git(*args):
        try:
            out = subprocess.run(["git", *args], cwd=repo_dir, capture_output=True, text=True, timeout=5)
        except (OSError, subprocess.SubprocessError):
            return None
        return out.stdout.strip() if out.returncode == 0 else None

    commit = git("rev-parse", "--short", "HEAD")
    status = git("status", "--porcelain", "--untracked-files=no")
    return {"git_commit": commit, "git_dirty": bool(status) if status is not None else None}

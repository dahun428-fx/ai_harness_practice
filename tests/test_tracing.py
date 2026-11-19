import json
from datetime import datetime, timedelta, timezone

from chatbot.tracing import TraceWriter, prompt_version


def fixed_clock(when: datetime):
    return lambda: when


NOW = datetime(2026, 11, 19, 14, 30, tzinfo=timezone.utc)


def read_lines(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_writes_one_json_line_per_record_into_daily_file(tmp_path):
    writer = TraceWriter(tmp_path / "traces", now=fixed_clock(NOW))

    writer.write({"type": "turn", "input": {"raw": "안녕"}})
    writer.write({"type": "turn", "input": {"raw": "두 번째"}})

    rows = read_lines(tmp_path / "traces" / "2026-11-19.jsonl")
    assert [r["input"]["raw"] for r in rows] == ["안녕", "두 번째"]


def test_adds_utc_iso_timestamp(tmp_path):
    writer = TraceWriter(tmp_path, now=fixed_clock(NOW))

    writer.write({"type": "turn"})

    assert read_lines(tmp_path / "2026-11-19.jsonl")[0]["time"] == "2026-11-19T14:30:00+00:00"


def test_keeps_korean_and_emoji_readable_in_file(tmp_path):
    writer = TraceWriter(tmp_path, now=fixed_clock(NOW))

    writer.write({"text": "안녕 \U0001F60A"})

    assert "안녕 \U0001F60A" in (tmp_path / "2026-11-19.jsonl").read_text(encoding="utf-8")


def test_broken_raw_input_with_lone_surrogates_round_trips(tmp_path):
    # 거부된 입력(INVALID_ENCODING)도 원문 그대로 남아야 나중에 eval 케이스로 옮길 수 있다
    broken = "\udcec븞\udceb"
    writer = TraceWriter(tmp_path, now=fixed_clock(NOW))

    writer.write({"input": {"raw": broken}})

    assert read_lines(tmp_path / "2026-11-19.jsonl")[0]["input"]["raw"] == broken


def test_bytes_values_are_stored_as_hex(tmp_path):
    writer = TraceWriter(tmp_path, now=fixed_clock(NOW))

    writer.write({"input": {"raw": b"\xec\x95"}})

    assert read_lines(tmp_path / "2026-11-19.jsonl")[0]["input"]["raw"] == {"bytes_hex": "ec95"}


def test_disabled_writer_writes_nothing(tmp_path):
    writer = TraceWriter(tmp_path / "traces", enabled=False, now=fixed_clock(NOW))

    writer.write({"type": "turn"})

    assert not (tmp_path / "traces").exists()


def test_cleanup_deletes_only_files_older_than_retention(tmp_path):
    for day in ("2026-10-01", "2026-10-20", "2026-11-19"):
        (tmp_path / f"{day}.jsonl").write_text("{}\n", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("keep", encoding="utf-8")
    writer = TraceWriter(tmp_path, now=fixed_clock(NOW))

    deleted = writer.cleanup(retention_days=30)

    assert sorted(p.name for p in deleted) == ["2026-10-01.jsonl"]
    assert sorted(p.name for p in tmp_path.iterdir()) == ["2026-10-20.jsonl", "2026-11-19.jsonl", "notes.txt"]


def test_cleanup_on_missing_directory_is_noop(tmp_path):
    writer = TraceWriter(tmp_path / "nope", now=fixed_clock(NOW - timedelta(days=1)))

    assert writer.cleanup(retention_days=30) == []


def test_prompt_version_is_short_stable_hash():
    assert prompt_version("너는 친절한 비서야") == prompt_version("너는 친절한 비서야")
    assert prompt_version("너는 친절한 비서야") != prompt_version("너는 엄격한 비서야")
    assert len(prompt_version("x")) == 12


def test_invisible_characters_are_escaped_so_labelers_can_see_them(tmp_path):
    writer = TraceWriter(tmp_path, now=fixed_clock(NOW))

    writer.write({"input": {"raw": "안녕\u200b"}})

    text = (tmp_path / "2026-11-19.jsonl").read_text(encoding="utf-8")
    assert "\\u200b" in text
    assert "\u200b" not in text

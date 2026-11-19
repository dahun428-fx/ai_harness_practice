"""사람이 읽을 수 있는 JSON Lines 직렬화.

한글·이모지는 그대로 두고, 화면에 보이지 않거나 헷갈리는 문자(zero-width, 제어문자, 특수 공백,
lone surrogate, 조합형 자모 등)만 \\uXXXX로 이스케이프한다. eval 케이스와 트레이스가 함께 쓴다.
"""

import json
import unicodedata

# 화면에서 보이지 않거나 다른 문자와 헷갈리는 문자는 파일에 \uXXXX로 남긴다
_ALWAYS_ESCAPE = set("\u00a0\u3000\u2028\u2029\ufffd")
_ESCAPE_CATEGORIES = {"Cc", "Cf", "Zs", "Zl", "Zp", "Mn", "Me", "Co", "Cn", "Cs"}


def _needs_escape(ch: str) -> bool:
    if ch == " ":
        return False
    if ch in _ALWAYS_ESCAPE:
        return True
    if 0x1100 <= ord(ch) <= 0x11FF:  # 조합형(NFD) 한글 자모
        return True
    return unicodedata.category(ch) in _ESCAPE_CATEGORIES


def _escape(ch: str) -> str:
    cp = ord(ch)
    if cp > 0xFFFF:
        cp -= 0x10000
        return "\\u%04x\\u%04x" % (0xD800 + (cp >> 10), 0xDC00 + (cp & 0x3FF))
    return "\\u%04x" % cp


def dumps(obj, default=None) -> str:
    """한글·이모지는 그대로, 보이지 않는 문자만 이스케이프한 JSON 한 줄."""
    raw = json.dumps(obj, ensure_ascii=False, default=default)
    return "".join(_escape(ch) if _needs_escape(ch) else ch for ch in raw)

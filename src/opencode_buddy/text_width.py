from __future__ import annotations

import unicodedata


def compact_text(text: str) -> str:
    return " ".join(text.split())


def display_width(text: str) -> int:
    return sum(_codepoint_width(char) for char in text)


def clip_text_by_width(text: str, limit: int, *, ellipsis: str = "...") -> str:
    compact = compact_text(text)
    if limit <= 0 or not compact:
        return ""
    if display_width(compact) <= limit:
        return compact

    ellipsis_width = display_width(ellipsis)
    if ellipsis_width >= limit:
        return _take_by_width(ellipsis, limit)

    return _take_by_width(compact, limit - ellipsis_width) + ellipsis


def _take_by_width(text: str, limit: int) -> str:
    if limit <= 0:
        return ""

    parts: list[str] = []
    width = 0
    for char in text:
        char_width = _codepoint_width(char)
        if width + char_width > limit:
            break
        parts.append(char)
        width += char_width
    return "".join(parts)


def _codepoint_width(char: str) -> int:
    if not char:
        return 0
    if unicodedata.combining(char):
        return 0

    category = unicodedata.category(char)
    if category.startswith("C"):
        return 0
    if unicodedata.east_asian_width(char) in {"W", "F"}:
        return 2
    return 1

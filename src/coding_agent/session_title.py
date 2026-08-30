"""Deterministic, local display titles for persisted sessions."""

from __future__ import annotations

import re
import unicodedata


MAX_SESSION_TITLE_CHARS = 30

_WHITESPACE = re.compile(r"\s+")
_MARKDOWN_PREFIX = re.compile(r"^(?:(?:#{1,6}|>|[-+*])\s+)+")
_POLITE_PREFIXES = (
    "请帮我",
    "我想让你",
    "能不能",
    "麻烦",
    "请你",
    "请",
)
_POLITE_SUFFIXES = ("谢谢", "一下")


def generate_session_title(first_user_task: str) -> str:
    """Return a bounded title without model calls or semantic guessing."""

    if type(first_user_task) is not str:
        raise TypeError("session title input must be text")
    title = _WHITESPACE.sub(" ", first_user_task.strip())
    title = _MARKDOWN_PREFIX.sub("", title).strip(" `*~")
    for prefix in _POLITE_PREFIXES:
        if title.startswith(prefix):
            title = title[len(prefix) :].lstrip()
            break
    title = _strip_terminal_punctuation(title)
    for suffix in _POLITE_SUFFIXES:
        if title.endswith(suffix):
            title = _strip_terminal_punctuation(title[: -len(suffix)])
            break
    title = title.strip(" `*~")
    if not title or not any(character.isalnum() for character in title):
        return "Untitled"
    if len(title) > MAX_SESSION_TITLE_CHARS:
        title = title[: MAX_SESSION_TITLE_CHARS - 1].rstrip() + "…"
    return title or "Untitled"


def _strip_terminal_punctuation(value: str) -> str:
    end = len(value)
    while end and (
        value[end - 1].isspace()
        or unicodedata.category(value[end - 1]).startswith("P")
    ):
        end -= 1
    return value[:end]

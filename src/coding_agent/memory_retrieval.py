"""Deterministic, dependency-free selection of relevant workspace memory."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .context_policy import ContextPolicy


_TOKEN = re.compile(r"[^\W_]+", re.UNICODE)
_MAX_SELECTED_ITEMS = 12
_KIND_PRIORITY = {
    "constraint": 5,
    "convention": 4,
    "architecture": 3,
    "command": 2,
    "fact": 1,
}


@dataclass(frozen=True, slots=True)
class ContextMemory:
    id: str
    kind: str
    key: str
    content: str


@dataclass(frozen=True, slots=True)
class MemorySelection:
    included: tuple[ContextMemory, ...]
    dropped_ids: tuple[str, ...]


def select_relevant_memory(
    items: tuple[ContextMemory, ...],
    original_task: str,
    latest_user: str,
    policy: ContextPolicy,
) -> MemorySelection:
    """Select whole entries by stable relevance rank, Top-K, and char budget."""

    if not items:
        return MemorySelection((), ())
    rendered_sizes = tuple(len(render_context_memory(item)) for item in items)
    if (
        len(items) <= _MAX_SELECTED_ITEMS
        and sum(rendered_sizes) + max(0, len(items) - 1) <= policy.memory_chars
    ):
        return MemorySelection(items, ())

    query = _tokens(f"{original_task} {latest_user}")
    ranked = sorted(
        enumerate(items),
        key=lambda indexed: (
            -_score(indexed[1], query),
            indexed[0],
        ),
    )
    included: list[ContextMemory] = []
    used = 0
    for _index, item in ranked:
        if len(included) == _MAX_SELECTED_ITEMS:
            break
        size = len(render_context_memory(item)) + (1 if included else 0)
        if used + size > policy.memory_chars:
            continue
        included.append(item)
        used += size
    included_ids = {item.id for item in included}
    dropped = tuple(item.id for item in items if item.id not in included_ids)
    return MemorySelection(tuple(included), dropped)


def render_context_memory(item: ContextMemory) -> str:
    return f"[{item.id}] ({item.kind}) {item.key} = {item.content}"


def _score(item: ContextMemory, query: set[str]) -> int:
    key_overlap = len(query & _tokens(item.key))
    content_overlap = len(query & _tokens(item.content))
    kind_overlap = int(item.kind.casefold() in query)
    return (
        key_overlap * 12
        + content_overlap * 6
        + kind_overlap * 3
        + _KIND_PRIORITY.get(item.kind, 0)
    )


def _tokens(text: str) -> set[str]:
    return {token.casefold() for token in _TOKEN.findall(text)}

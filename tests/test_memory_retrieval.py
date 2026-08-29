from __future__ import annotations

from coding_agent.context_policy import ContextPolicy
from coding_agent.context import ContextManager, ConversationHistory
from coding_agent.memory_retrieval import ContextMemory, select_relevant_memory
from coding_agent.protocol import Message, Role


def _item(index: int, kind: str, key: str, content: str) -> ContextMemory:
    return ContextMemory(f"{index:08x}", kind, key, content)


def test_small_memory_keeps_storage_order_when_it_fits() -> None:
    items = (
        _item(1, "fact", "source.root", "src"),
        _item(2, "constraint", "constraint.vendor", "do not modify vendor"),
    )

    selection = select_relevant_memory(
        items,
        "unrelated original task",
        "unrelated latest message",
        ContextPolicy(memory_chars=8_000),
    )

    assert selection.included == items
    assert selection.dropped_ids == ()


def test_large_memory_ranks_key_content_and_kind_overlap_deterministically() -> None:
    items = tuple(
        _item(index, "fact", f"ordinary.item-{index}", f"ordinary value {index}")
        for index in range(13)
    ) + (
        _item(20, "command", "test.command", "python -m pytest -q"),
        _item(21, "architecture", "build.system", "cmake"),
    )
    policy = ContextPolicy(memory_chars=8_000)

    first = select_relevant_memory(
        items, "repair the cmake project", "run the pytest test command", policy
    )
    second = select_relevant_memory(
        items, "repair the cmake project", "run the pytest test command", policy
    )

    assert first == second
    assert len(first.included) == 12
    assert [item.id for item in first.included[:2]] == ["00000014", "00000015"]
    assert set(first.dropped_ids) == {
        item.id for item in items if item not in first.included
    }


def test_constraint_priority_wins_a_zero_overlap_tie_and_storage_ties_are_stable() -> None:
    items = tuple(
        _item(index, "fact", f"fact.item-{index}", f"value {index}")
        for index in range(12)
    ) + (
        _item(20, "constraint", "constraint.vendor", "do not modify vendor"),
        _item(21, "constraint", "constraint.generated", "do not edit generated files"),
    )

    selection = select_relevant_memory(
        items,
        "unrelated",
        "unrelated",
        ContextPolicy(memory_chars=8_000),
    )

    assert [item.id for item in selection.included[:2]] == ["00000014", "00000015"]
    assert [item.id for item in selection.included[2:]] == [
        f"{index:08x}" for index in range(10)
    ]


def test_character_budget_never_slices_an_entry_and_reports_drops() -> None:
    items = (
        _item(1, "constraint", "constraint.vendor", "x" * 90),
        _item(2, "fact", "source.root", "src"),
    )
    selection = select_relevant_memory(
        items,
        "source root",
        "source root",
        ContextPolicy(memory_chars=80),
    )

    assert selection.included == (items[1],)
    assert selection.dropped_ids == (items[0].id,)


def test_context_manager_uses_original_and_latest_user_for_structured_memory() -> None:
    items = tuple(
        _item(index, "fact", f"ordinary.item-{index}", f"value {index}")
        for index in range(13)
    ) + (_item(20, "command", "test.command", "pytest"),)
    history = ConversationHistory("core", "repair tests")
    history.append(Message(Role.ASSISTANT, "initial"))
    history.append(Message(Role.USER, "run pytest now"))
    manager = ContextManager(policy=ContextPolicy(memory_chars=8_000))
    manager.set_workspace_memories(items)

    view = manager.build(history)

    memory = next(
        message.content or ""
        for message in view
        if "Workspace memory" in (message.content or "")
    )
    assert "test.command = pytest" in memory
    assert items[-1].id in {item.id for item in manager.last_memory_selection.included}
    assert manager.last_memory_selection.dropped_ids

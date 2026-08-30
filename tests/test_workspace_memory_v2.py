from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from coding_agent.memory import MemoryMatch, WorkspaceMemoryStore
from coding_agent.memory_candidate import (
    MemoryCandidate,
    MemoryCandidateExtractor,
    MemoryEvidence,
    is_safe_candidate,
)
from coding_agent.protocol import Message, ModelTurn, Role
from coding_agent.session import SessionError
from fakes import FakeModelClient


NOW = datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc)


def _store(tmp_path: Path) -> WorkspaceMemoryStore:
    return WorkspaceMemoryStore(
        tmp_path / "home",
        clock=lambda: NOW,
        id_generator=iter(("11111111", "22222222", "33333333")).__next__,
    )


def test_structured_memory_persists_required_v3_fields(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = _store(tmp_path)

    item = store.add(
        workspace,
        "python -m pytest -q",
        (),
        key="test.command",
        kind="command",
        source="confirmed_candidate",
    )
    payload = json.loads(next((store.root / "memories").glob("*.json")).read_text())

    assert item.key == "test.command"
    assert item.content == "python -m pytest -q"
    assert item.text == "python -m pytest -q"
    assert payload["schema_version"] == 3
    assert payload["items"][0] == {
        "id": "11111111",
        "kind": "command",
        "key": "test.command",
        "content": "python -m pytest -q",
        "source": "confirmed_candidate",
        "created_at": "2026-08-29T12:00:00Z",
        "updated_at": "2026-08-29T12:00:00Z",
    }


def test_v2_text_memory_migrates_deterministically_on_next_write(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = _store(tmp_path)
    store.add(workspace, "placeholder", ())
    path = next((store.root / "memories").glob("*.json"))
    path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "workspace": str(workspace.resolve()),
                "items": [
                    {
                        "id": "11111111",
                        "text": "Test command: pytest",
                        "kind": "command",
                        "source": "user",
                        "created_at": "2026-08-29T12:00:00Z",
                        "updated_at": "2026-08-29T12:00:00Z",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    migrated = store.list(workspace)[0]
    store.add(workspace, "src", (), key="source.root", kind="architecture")
    persisted = json.loads(path.read_text(encoding="utf-8"))

    assert migrated.key == "test.command"
    assert migrated.content == "pytest"
    assert persisted["schema_version"] == 3
    assert "text" not in persisted["items"][0]


def test_exact_normalized_dedup_and_key_conflict_are_distinct(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = _store(tmp_path)
    original = store.add(
        workspace,
        "pytest",
        (),
        key="test.command",
        kind="command",
    )

    assert store.match(workspace, "pytest", "command", key="test.command") == MemoryMatch(
        "exact_duplicate", original
    )
    assert store.match(
        workspace, "  PYTEST  ", "command", key="test.command"
    ) == MemoryMatch("normalized_duplicate", original)
    assert store.match(
        workspace, "python -m pytest -q", "command", key="test.command"
    ) == MemoryMatch("conflict", original)


@pytest.mark.parametrize(
    "content",
    (
        "API_KEY=synthetic-secret-value",
        "Authorization: Bearer abcdefghijklmnop",
        "password: hunter2-secret",
    ),
)
def test_store_rejects_unredacted_secret_shaped_content(
    tmp_path: Path, content: str
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    with pytest.raises(SessionError) as raised:
        _store(tmp_path).add(workspace, content, (), key="project.fact")

    assert raised.value.error_code == "MEMORY_INVALID"


def test_candidate_uses_key_content_schema_and_rejects_invalid_or_secret_values() -> None:
    model = FakeModelClient(
        [
            ModelTurn(
                '{"candidates":['
                '{"key":"test.command","content":"ctest",'
                '"kind":"command","source":"TOOL_VERIFIED",'
                '"evidence":{"tool_name":"execute_command","command":"ctest",'
                '"success":true}},'
                '{"key":"Bad Key","content":"ignored",'
                '"kind":"fact","source":"MODEL_INFERRED","evidence":{}},'
                '{"key":"auth.token","content":"token=abcdefghijklmnop",'
                '"kind":"fact","source":"MODEL_INFERRED","evidence":{}'
                '}'
                "]}"
            )
        ]
    )

    candidates = MemoryCandidateExtractor(model).extract(
        (Message(Role.USER, "the project must always run tests"),)
    )

    assert candidates == (
        MemoryCandidate(
            "test.command",
            "ctest",
            "command",
            "TOOL_VERIFIED",
            MemoryEvidence(
                tool_name="execute_command", command="ctest", success=True
            ),
        ),
    )
    assert is_safe_candidate(candidates[0], ()) is True


def test_schema_v3_duplicate_keys_and_oversized_total_are_corrupt(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = _store(tmp_path)
    store.add(workspace, "pytest", (), key="test.command", kind="command")
    path = next((store.root / "memories").glob("*.json"))
    payload = json.loads(path.read_text(encoding="utf-8"))

    duplicate = dict(payload["items"][0], id="22222222", content="ctest")
    payload["items"].append(duplicate)
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(SessionError) as duplicate_error:
        store.list(workspace)
    assert duplicate_error.value.error_code == "MEMORY_CORRUPT"

    payload["items"] = [
        {
            **payload["items"][0],
            "id": f"{index:08x}",
            "key": f"fact.item-{index}",
            "content": "x" * 2_000,
        }
        for index in range(26)
    ]
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(SessionError) as size_error:
        store.list(workspace)
    assert size_error.value.error_code == "MEMORY_CORRUPT"

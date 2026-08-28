from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path

import pytest

from coding_agent.cli import main
from coding_agent.model import ModelClient
from coding_agent.protocol import Message, ModelTurn, Role
from coding_agent.session_store import JsonSessionStore
from coding_agent.system_prompt import SYSTEM_PROMPT
from tests.fakes import FakeModelClient


FAKE_PROVIDER_KEY = "fake-task7-provider-key"
NOW = datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc)


class _ClosableFakeModelClient(FakeModelClient):
    def __init__(self, script: Iterable[ModelTurn | Exception]) -> None:
        super().__init__(script)
        self.close_count = 0

    def close(self) -> None:
        self.close_count += 1


class _ClientFactory:
    def __init__(self, client: ModelClient) -> None:
        self.client = client
        self.calls: list[tuple[str, str, str, str]] = []

    def __call__(
        self,
        base_url: str,
        model: str,
        api_key: str,
        thinking_mode: str,
    ) -> ModelClient:
        self.calls.append((base_url, model, api_key, thinking_mode))
        return self.client


class _InputReader:
    def __init__(self, *values: str) -> None:
        self._values = iter(values)

    def __call__(self, _prompt: str) -> str:
        return next(self._values)


class _StoreFactory:
    def __init__(self, *session_ids: str) -> None:
        self._session_ids = iter(session_ids)
        self.roots: list[Path] = []

    def __call__(self, root: Path) -> JsonSessionStore:
        self.roots.append(root)
        return JsonSessionStore(
            root,
            clock=lambda: NOW,
            id_generator=lambda: next(self._session_ids),
        )


def _arguments(workspace: Path, *session_arguments: str) -> list[str]:
    return [
        "--workspace",
        str(workspace),
        "--base-url",
        "https://example.test/v1",
        "--model",
        "test-model",
        *session_arguments,
    ]


def _environment(session_home: Path) -> dict[str, str]:
    return {
        "OPENAI_API_KEY": FAKE_PROVIDER_KEY,
        "CODING_AGENT_HOME": str(session_home),
    }


def _run(
    workspace: Path,
    session_home: Path,
    store_factory: _StoreFactory,
    client_factory: _ClientFactory,
    inputs: _InputReader,
    *session_arguments: str,
) -> int:
    return main(
        _arguments(workspace, *session_arguments),
        environ=_environment(session_home),
        client_factory=client_factory,
        session_store_factory=store_factory,
        input_reader=inputs,
    )


def _assert_constructed_and_closed_once(
    factory: _ClientFactory,
    client: _ClosableFakeModelClient,
) -> None:
    assert factory.calls == [
        (
            "https://example.test/v1",
            "test-model",
            FAKE_PROVIDER_KEY,
            "provider-default",
        )
    ]
    assert len(client.calls) == 1
    assert client.close_count == 1


def test_default_interactive_restart_resumes_disk_history(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    session_home = tmp_path / "session-home"
    session_id = "101010101010"
    store_factory = _StoreFactory(session_id)
    first_client = _ClosableFakeModelClient([ModelTurn("first answer")])
    first_factory = _ClientFactory(first_client)

    first_exit = _run(
        workspace,
        session_home,
        store_factory,
        first_factory,
        _InputReader("first task", "/exit"),
    )
    first_output = capsys.readouterr()

    second_client = _ClosableFakeModelClient([ModelTurn("second answer")])
    second_factory = _ClientFactory(second_client)
    second_exit = _run(
        workspace,
        session_home,
        store_factory,
        second_factory,
        _InputReader("follow-up", "/exit"),
    )
    second_output = capsys.readouterr()

    assert (first_exit, second_exit) == (0, 0)
    assert f"[session] created: {session_id}" in first_output.out
    assert f"[session] resumed: {session_id}" in second_output.out
    assert first_output.err == second_output.err == ""
    assert second_client.calls[0][0] == (
        Message(Role.SYSTEM, SYSTEM_PROMPT),
        Message(Role.USER, "first task"),
        Message(Role.ASSISTANT, "first answer"),
        Message(Role.USER, "follow-up"),
    )
    _assert_constructed_and_closed_once(first_factory, first_client)
    _assert_constructed_and_closed_once(second_factory, second_client)
    assert store_factory.roots == [session_home, session_home]

    store = JsonSessionStore(session_home)
    saved = store.load_session(session_id, workspace)
    assert saved.messages == (
        Message(Role.USER, "first task"),
        Message(Role.ASSISTANT, "first answer"),
        Message(Role.USER, "follow-up"),
        Message(Role.ASSISTANT, "second answer"),
    )
    assert store.load_latest(workspace).session_id == session_id

    session_path = session_home / "sessions" / f"{session_id}.json"
    session_text = session_path.read_text(encoding="utf-8")
    session_payload = json.loads(session_text)
    assert [message["role"] for message in session_payload["messages"]] == [
        "user",
        "assistant",
        "user",
        "assistant",
    ]
    assert FAKE_PROVIDER_KEY not in session_text

    index_paths = list((session_home / "workspaces").glob("*.json"))
    assert len(index_paths) == 1
    index_payload = json.loads(index_paths[0].read_text(encoding="utf-8"))
    assert index_payload["latest_session_id"] == session_id
    assert index_payload["session_ids"] == [session_id]


def test_new_explicit_resume_and_workspace_isolation_round_trip_on_disk(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace = tmp_path / "workspace"
    other_workspace = tmp_path / "other-workspace"
    workspace.mkdir()
    other_workspace.mkdir()
    session_home = tmp_path / "session-home"
    older_id = "111111111111"
    newer_id = "222222222222"
    store_factory = _StoreFactory(older_id, newer_id)

    older_client = _ClosableFakeModelClient([ModelTurn("older answer")])
    older_factory = _ClientFactory(older_client)
    assert _run(
        workspace,
        session_home,
        store_factory,
        older_factory,
        _InputReader("older task", "/exit"),
    ) == 0
    first_output = capsys.readouterr()
    assert f"[session] created: {older_id}" in first_output.out
    _assert_constructed_and_closed_once(older_factory, older_client)

    newer_client = _ClosableFakeModelClient([ModelTurn("newer answer")])
    newer_factory = _ClientFactory(newer_client)
    assert _run(
        workspace,
        session_home,
        store_factory,
        newer_factory,
        _InputReader("newer task", "/exit"),
        "--new-session",
    ) == 0
    second_output = capsys.readouterr()
    assert f"[session] created: {newer_id}" in second_output.out
    _assert_constructed_and_closed_once(newer_factory, newer_client)
    assert JsonSessionStore(session_home).load_latest(workspace).session_id == newer_id

    resumed_client = _ClosableFakeModelClient([ModelTurn("older follow-up answer")])
    resumed_factory = _ClientFactory(resumed_client)
    assert _run(
        workspace,
        session_home,
        store_factory,
        resumed_factory,
        _InputReader("older follow-up", "/exit"),
        "--resume-session",
        older_id,
    ) == 0
    resumed_output = capsys.readouterr()

    assert f"[session] resumed: {older_id}" in resumed_output.out
    assert resumed_client.calls[0][0] == (
        Message(Role.SYSTEM, SYSTEM_PROMPT),
        Message(Role.USER, "older task"),
        Message(Role.ASSISTANT, "older answer"),
        Message(Role.USER, "older follow-up"),
    )
    _assert_constructed_and_closed_once(resumed_factory, resumed_client)

    rejected_client = _ClosableFakeModelClient([])
    rejected_factory = _ClientFactory(rejected_client)
    rejected_exit = _run(
        other_workspace,
        session_home,
        store_factory,
        rejected_factory,
        _InputReader(),
        "--resume-session",
        older_id,
    )
    rejected_output = capsys.readouterr()

    assert rejected_exit == 7
    assert rejected_output.out == ""
    assert "SESSION_WORKSPACE_MISMATCH" in rejected_output.err
    assert rejected_factory.calls == []
    assert rejected_client.close_count == 0

    store = JsonSessionStore(session_home)
    older = store.load_session(older_id, workspace)
    newer = store.load_session(newer_id, workspace)
    assert older.messages == (
        Message(Role.USER, "older task"),
        Message(Role.ASSISTANT, "older answer"),
        Message(Role.USER, "older follow-up"),
        Message(Role.ASSISTANT, "older follow-up answer"),
    )
    assert newer.messages == (
        Message(Role.USER, "newer task"),
        Message(Role.ASSISTANT, "newer answer"),
    )
    assert store.load_latest(workspace).session_id == older_id

    session_paths = sorted((session_home / "sessions").glob("*.json"))
    assert [path.stem for path in session_paths] == [older_id, newer_id]
    for session_path in session_paths:
        text = session_path.read_text(encoding="utf-8")
        payload = json.loads(text)
        assert FAKE_PROVIDER_KEY not in text
        assert all(
            message["role"] != "system" for message in payload["messages"]
        )

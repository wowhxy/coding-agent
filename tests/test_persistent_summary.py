from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from coding_agent.agent import AgentRunner
from coding_agent.context import ContextManager, ConversationHistory
from coding_agent.interactive import InteractiveSession
from coding_agent.protocol import Message, ModelTurn, Role
from coding_agent.session import SessionRecord, deserialize_session, serialize_session
from coding_agent.session import SessionError
from coding_agent.session_store import JsonSessionStore
from coding_agent.summary import SummaryManager, SummaryState
from coding_agent.tools.registry import ToolRegistry
from fakes import FakeModelClient


CREATED = datetime(2026, 8, 29, 8, 0, tzinfo=timezone.utc)
UPDATED = datetime(2026, 8, 29, 8, 5, tzinfo=timezone.utc)
SECRET = "unit-test-provider-secret"


def _messages() -> tuple[Message, ...]:
    return (
        Message(Role.USER, "original goal"),
        Message(Role.ASSISTANT, "old answer"),
        Message(Role.USER, "recent question"),
        Message(Role.ASSISTANT, "recent answer"),
    )


def _record(workspace: Path) -> SessionRecord:
    return SessionRecord(
        session_id="111111111111",
        workspace=workspace.resolve(),
        provider="provider",
        model="model",
        created_at=CREATED,
        updated_at=UPDATED,
        messages=_messages(),
        summary=SummaryState("existing summary", 1, UPDATED),
    )


def test_session_v3_round_trip_persists_summary_and_v2_migrates_to_none(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    record = _record(workspace)

    payload = json.loads(serialize_session(record))
    restored = deserialize_session(json.dumps(payload))
    v2 = dict(payload)
    v2["schema_version"] = 2
    del v2["summary"]

    assert payload["schema_version"] == 3
    assert payload["summary"] == {
        "text": "existing summary",
        "covered_message_count": 1,
        "updated_at": "2026-08-29T08:05:00Z",
    }
    assert restored == record
    assert deserialize_session(json.dumps(v2)).summary is None


def test_corrupt_or_out_of_range_summary_is_dropped_without_losing_history(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    payload = json.loads(serialize_session(_record(workspace)))

    corrupt = dict(payload)
    corrupt["summary"] = {"text": 7}
    out_of_range = dict(payload)
    out_of_range["summary"] = {
        "text": "bad coverage",
        "covered_message_count": 99,
        "updated_at": "2026-08-29T08:05:00Z",
    }

    assert deserialize_session(json.dumps(corrupt)).messages == _messages()
    assert deserialize_session(json.dumps(corrupt)).summary is None
    assert deserialize_session(json.dumps(out_of_range)).messages == _messages()
    assert deserialize_session(json.dumps(out_of_range)).summary is None


def test_summary_is_saved_recovered_and_only_newly_old_messages_are_processed(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    times = iter((CREATED, UPDATED, UPDATED, UPDATED))
    store = JsonSessionStore(
        tmp_path / "home",
        clock=lambda: next(times),
        id_generator=lambda: "111111111111",
    )
    initial = store.save(
        SessionRecord(
            "111111111111",
            workspace.resolve(),
            "provider",
            "model",
            CREATED,
            CREATED,
            _messages(),
        )
    )
    history = ConversationHistory.from_persisted("system", initial.messages)
    first_summary_model = FakeModelClient([ModelTurn(f"summary {SECRET}")])
    first_runner = AgentRunner(
        FakeModelClient([ModelTurn("first done")]),
        ToolRegistry(),
        ContextManager(max_context_chars=4_000, recent_turns=1),
        summary_manager=SummaryManager(
            first_summary_model,
            threshold_chars=1,
            recent_turns=1,
            clock=lambda: UPDATED,
        ),
    )
    interactive = InteractiveSession(
        first_runner,
        history,
        initial,
        store,
        "provider",
        "model",
        (SECRET,),
    )
    before = history.messages

    interactive.execute("new task")
    persisted = store.load_session(initial.session_id, workspace)

    assert history.messages == before
    assert interactive.history.messages[: len(before)] == before
    assert persisted.summary == SummaryState("summary [REDACTED]", 3, UPDATED)

    resumed_history = ConversationHistory.from_persisted("system", persisted.messages)
    second_summary_model = FakeModelClient([ModelTurn("summary two")])
    second_runner = AgentRunner(
        FakeModelClient([ModelTurn("second done")]),
        ToolRegistry(),
        ContextManager(max_context_chars=4_000, recent_turns=1),
        summary_manager=SummaryManager(
            second_summary_model,
            threshold_chars=1,
            recent_turns=1,
            clock=lambda: UPDATED,
        ),
    )
    second_runner.restore_summary_state(persisted.summary)

    second_runner.run_turn(resumed_history, "follow-up")

    request = second_summary_model.calls[0][0][-1].content or ""
    assert "summary [REDACTED]" in request
    assert "new task" in request and "first done" in request
    assert "old answer" not in request
    assert second_runner.summary_state == SummaryState("summary two", 5, UPDATED)


def test_summary_state_rolls_back_when_atomic_session_save_fails(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    record = _record(workspace)
    history = ConversationHistory.from_persisted("system", record.messages)
    summary_model = FakeModelClient([ModelTurn("advanced summary")])
    runner = AgentRunner(
        FakeModelClient([ModelTurn("done")]),
        ToolRegistry(),
        ContextManager(max_context_chars=4_000, recent_turns=1),
        summary_manager=SummaryManager(
            summary_model,
            threshold_chars=1,
            recent_turns=1,
            clock=lambda: UPDATED,
        ),
    )
    runner.restore_summary_state(record.summary)

    class FailingStore:
        def save(self, _record: SessionRecord) -> SessionRecord:
            raise SessionError("SESSION_SAVE_FAILED", "synthetic failure")

    interactive = InteractiveSession(
        runner,
        history,
        record,
        FailingStore(),
        "provider",
        "model",
        (),
    )

    try:
        interactive.execute("new task")
    except SessionError:
        pass
    else:
        raise AssertionError("save failure expected")

    assert runner.summary_state == record.summary
    assert interactive.history.messages == history.messages

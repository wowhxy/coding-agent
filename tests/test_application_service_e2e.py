from __future__ import annotations

import json
from pathlib import Path

from coding_agent.application.events import ProductEventKind
from coding_agent.application.service import CodingAgentService
from coding_agent.config import RuntimeConfig
from coding_agent.protocol import ModelTurn, RunStatus, ToolCall
from tests.fakes import FakeModelClient


def test_real_core_tools_surface_activity_changes_and_verification(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    home = tmp_path / "home"
    workspace.mkdir()
    (workspace / "test_answer.py").write_text(
        "from answer import answer\n\ndef test_answer():\n    assert answer() == 42\n",
        encoding="utf-8",
    )
    config = RuntimeConfig(
        workspace.resolve(), "https://example.test/v1", "fake", "secret", "FAKE_KEY",
        "disabled", frozenset({"FAKE_KEY"}), 8, 20_000, 4, 2_000, 10,
    )
    client = FakeModelClient(
        [
            ModelTurn(tool_calls=(ToolCall("w1", "write_file", json.dumps({"path": "answer.py", "content": "def answer():\n    return 42\n"})),)),
            ModelTurn(tool_calls=(ToolCall("t1", "execute_command", json.dumps({"command": "python -m pytest -q"})),)),
            ModelTurn("Implemented and verified."),
            ModelTurn('{"candidates":[]}'),
        ]
    )
    service = CodingAgentService.create(config, "custom", home, lambda *_args: client)
    events = []
    service.subscribe(events.append)

    result = service.submit_task("create answer implementation and run tests")
    snapshot = service.snapshot()

    assert result.status is RunStatus.FINAL_RESPONSE
    assert (workspace / "answer.py").exists()
    assert [item.path for item in snapshot.changes] == ["answer.py"]
    assert snapshot.verifications[-1].ok is True
    assert "passed" in snapshot.verifications[-1].summary
    assert [item.title for item in snapshot.activities] == ["write_file", "execute_command"]
    assert any(event.kind is ProductEventKind.TOOL_STARTED for event in events)
    assert any(event.kind is ProductEventKind.FILE_CHANGES for event in events)
    assert any(event.kind is ProductEventKind.VERIFICATION for event in events)
    service.close()

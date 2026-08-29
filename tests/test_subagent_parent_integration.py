from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys

from coding_agent.agent import AgentRunner
from coding_agent.config import resolve_config
from coding_agent.context import ContextManager, ConversationHistory
from coding_agent.protocol import Message, ModelTurn, Role, RunStatus, ToolCall
from coding_agent.subagents.control import create_delegate_tasks_tool
from coding_agent.subagents.manager import SubagentManager
from coding_agent.system_prompt import SYSTEM_PROMPT
from coding_agent.tools import build_default_registry
from fakes import FakeModelClient


def _python_command(*arguments: str) -> str:
    parts = [sys.executable, *arguments]
    return subprocess.list2cmdline(parts) if os.name == "nt" else shlex.join(parts)


def test_parent_delegates_then_remains_the_only_writer_and_verifier(tmp_path) -> None:
    source = tmp_path / "parser.py"
    source.write_text("VALUE = 'old'\n", encoding="utf-8")
    child = FakeModelClient(
        [
            ModelTurn(
                tool_calls=(
                    ToolCall("child-read", "read_file", '{"path":"parser.py"}'),
                )
            ),
            ModelTurn("parser.py contains the old value"),
        ]
    )
    manager = SubagentManager(tmp_path, lambda: child)
    parent = FakeModelClient(
        [
            ModelTurn(
                tool_calls=(
                    ToolCall(
                        "parent-delegate",
                        "delegate_tasks",
                        json.dumps(
                            {
                                "tasks": [
                                    {
                                        "task": "Inspect parser.py",
                                        "role": "explore",
                                        "context_mode": "fork",
                                    }
                                ]
                            }
                        ),
                    ),
                )
            ),
            ModelTurn(
                tool_calls=(
                    ToolCall(
                        "parent-edit",
                        "replace_in_file",
                        json.dumps(
                            {
                                "path": "parser.py",
                                "old_text": "VALUE = 'old'",
                                "new_text": "VALUE = 'fixed'",
                            }
                        ),
                    ),
                )
            ),
            ModelTurn(
                tool_calls=(
                    ToolCall(
                        "parent-verify",
                        "execute_command",
                        json.dumps(
                            {
                                "command": _python_command(
                                    "-c",
                                    "from parser import VALUE; assert VALUE == 'fixed'",
                                )
                            }
                        ),
                    ),
                )
            ),
            ModelTurn("Parent edited and verified the fix."),
        ]
    )
    config = resolve_config(
        workspace=tmp_path,
        base_url="https://example.test/v1",
        model="fake",
        environ={"OPENAI_API_KEY": "fake-key"},
    )
    registry = build_default_registry(config)
    registry.register_many(
        (create_delegate_tasks_tool(manager),), source="control:subagent"
    )
    runner = AgentRunner(
        parent,
        registry,
        ContextManager(),
        run_start_hook=manager.begin_parent_run,
        context_snapshot_sink=manager.observe_parent_context,
    )
    history = ConversationHistory(SYSTEM_PROMPT)

    result = runner.run_turn(history, "Inspect and fix parser.py, then verify it.")

    assert result.status is RunStatus.FINAL_RESPONSE
    assert source.read_text(encoding="utf-8") == "VALUE = 'fixed'\n"
    assert registry.source_of("delegate_tasks") == "control:subagent"
    parent_tool_calls = tuple(
        call
        for message in history.messages
        if message.role is Role.ASSISTANT
        for call in message.tool_calls
    )
    assert tuple(call.id for call in parent_tool_calls) == (
        "parent-delegate",
        "parent-edit",
        "parent-verify",
    )
    tool_messages = tuple(
        message for message in history.messages if message.role is Role.TOOL
    )
    assert tuple(message.tool_call_id for message in tool_messages) == (
        "parent-delegate",
        "parent-edit",
        "parent-verify",
    )
    assert "child-read" not in "\n".join(
        message.content or "" for message in history.messages
    )
    delegated = json.loads(tool_messages[0].content or "{}")
    assert delegated["ok"] is True
    payload = json.loads(delegated["output"])
    assert payload["results"][0]["result"] == "parser.py contains the old value"
    assert any(
        "Bounded parent context snapshot" in (message.content or "")
        for message in child.calls[0][0]
    )


def test_parent_system_prompt_keeps_delegation_subordinate_to_verification() -> None:
    assert "delegate" in SYSTEM_PROMPT.casefold()
    assert "parent" in SYSTEM_PROMPT.casefold()
    assert "independent" in SYSTEM_PROMPT.casefold()
    assert "tests" in SYSTEM_PROMPT.casefold()


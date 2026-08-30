from __future__ import annotations

import pytest

from coding_agent.application.commands import (
    CommandAction,
    CommandError,
    CommandName,
    command_help,
    parse_command,
)


@pytest.mark.parametrize(
    ("text", "name", "action", "argument"),
    (
        ("/new", CommandName.SESSION, CommandAction.NEW, ""),
        ("/rename parser fix", CommandName.SESSION, CommandAction.RENAME, "parser fix"),
        ("/delete", CommandName.SESSION, CommandAction.DELETE, ""),
        ("/sessions", CommandName.SESSION, CommandAction.LIST, ""),
        ("/session abc123", CommandName.SESSION, CommandAction.SWITCH, "abc123"),
        ("/session search parser", CommandName.SESSION, CommandAction.SEARCH, "parser"),
        ("/memory", CommandName.MEMORY, CommandAction.LIST, ""),
        ("/memory add test.command = pytest -q", CommandName.MEMORY, CommandAction.ADD, "test.command = pytest -q"),
        ("/memory delete abcd1234", CommandName.MEMORY, CommandAction.DELETE, "abcd1234"),
        ("/memory clear", CommandName.MEMORY, CommandAction.CLEAR, ""),
        ("/skills", CommandName.SKILL, CommandAction.LIST, ""),
        ("/skill use tdd", CommandName.SKILL, CommandAction.USE, "tdd"),
        ("/skill off tdd", CommandName.SKILL, CommandAction.OFF, "tdd"),
        ("/skill clear", CommandName.SKILL, CommandAction.CLEAR, ""),
        ("/plugins", CommandName.PLUGIN, CommandAction.LIST, ""),
        ("/plugin enable git-readonly", CommandName.PLUGIN, CommandAction.ENABLE, "git-readonly"),
        ("/plugin disable git-readonly", CommandName.PLUGIN, CommandAction.DISABLE, "git-readonly"),
        ("/recall unicode parser", CommandName.RECALL, CommandAction.SEARCH, "unicode parser"),
        ("/help", CommandName.HELP, CommandAction.SHOW, ""),
    ),
)
def test_required_slash_commands_parse_to_typed_actions(
    text: str, name: CommandName, action: CommandAction, argument: str
) -> None:
    command = parse_command(text)

    assert command is not None
    assert (command.name, command.action, command.argument) == (name, action, argument)


def test_normal_task_is_not_a_slash_command() -> None:
    assert parse_command("fix the parser") is None


@pytest.mark.parametrize("text", ("/", "/unknown", "/rename", "/memory add", "/help now"))
def test_invalid_commands_have_concise_stable_errors(text: str) -> None:
    with pytest.raises(CommandError) as raised:
        parse_command(text)

    assert raised.value.code == "COMMAND_INVALID"
    if len(text) > 1:
        assert text not in raised.value.message
    assert "/help" in raised.value.message


def test_help_is_discoverable_and_covers_every_command_group() -> None:
    entries = command_help()
    usages = "\n".join(item.usage for item in entries)

    for command in ("/new", "/session", "/memory", "/skill", "/plugin", "/recall", "/help"):
        assert command in usages
    assert all(item.description.strip() for item in entries)

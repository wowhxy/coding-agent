"""Small deterministic slash-command grammar for product front ends."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class CommandName(str, Enum):
    SESSION = "session"
    MEMORY = "memory"
    SKILL = "skill"
    PLUGIN = "plugin"
    RECALL = "recall"
    HELP = "help"


class CommandAction(str, Enum):
    SHOW = "show"
    NEW = "new"
    RENAME = "rename"
    DELETE = "delete"
    LIST = "list"
    SEARCH = "search"
    SWITCH = "switch"
    ADD = "add"
    CLEAR = "clear"
    USE = "use"
    OFF = "off"
    ENABLE = "enable"
    DISABLE = "disable"


@dataclass(frozen=True, slots=True)
class ProductCommand:
    name: CommandName
    action: CommandAction
    argument: str = ""


@dataclass(frozen=True, slots=True)
class CommandHelp:
    usage: str
    description: str


@dataclass(frozen=True, slots=True)
class CommandSuggestion:
    value: str
    description: str


class CommandError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


_HELP = (
    CommandHelp("/new | /rename <name> | /delete", "Create, name, or delete a session."),
    CommandHelp("/sessions | /session <id> | /session search <query>", "List, switch, or search sessions."),
    CommandHelp("/memory [add <text> | delete <id> | clear]", "Manage workspace Memory."),
    CommandHelp("/skills | /skill use <name> | off <name> | clear", "Manage active Skills."),
    CommandHelp("/plugins | /plugin enable <name> | disable <name>", "Manage trusted local Plugins."),
    CommandHelp("/recall <query>", "Search prior sessions in this workspace."),
    CommandHelp("/help", "Show commands and keyboard shortcuts."),
)

_TOP_LEVEL_SUGGESTIONS = (
    CommandSuggestion("/new", "Create a new session"),
    CommandSuggestion("/sessions", "Show sessions"),
    CommandSuggestion("/session ", "Switch or search sessions"),
    CommandSuggestion("/rename ", "Rename the active session"),
    CommandSuggestion("/delete", "Delete the active session"),
    CommandSuggestion("/memory ", "Manage workspace memory"),
    CommandSuggestion("/skills", "Manage Skills"),
    CommandSuggestion("/skill ", "Activate or deactivate Skills"),
    CommandSuggestion("/plugins", "Manage trusted Plugins"),
    CommandSuggestion("/plugin ", "Enable or disable a Plugin"),
    CommandSuggestion("/recall ", "Search prior sessions"),
    CommandSuggestion("/help", "Show help"),
)

_SUBCOMMAND_SUGGESTIONS = {
    "/session ": (
        CommandSuggestion("/session search ", "Search sessions"),
    ),
    "/memory ": (
        CommandSuggestion("/memory add ", "Add workspace memory"),
        CommandSuggestion("/memory delete ", "Delete memory by ID"),
        CommandSuggestion("/memory clear", "Clear workspace memory"),
    ),
    "/skill ": (
        CommandSuggestion("/skill use ", "Activate a Skill"),
        CommandSuggestion("/skill off ", "Deactivate a Skill"),
        CommandSuggestion("/skill clear", "Deactivate all Skills"),
    ),
    "/plugin ": (
        CommandSuggestion("/plugin enable ", "Enable a trusted Plugin"),
        CommandSuggestion("/plugin disable ", "Disable a Plugin"),
    ),
}


def command_help() -> tuple[CommandHelp, ...]:
    return _HELP


def command_suggestions(text: str) -> tuple[CommandSuggestion, ...]:
    """Return deterministic completions for a single-line slash prefix."""

    if type(text) is not str:
        raise TypeError("suggestion text must be a string")
    if not text.startswith("/") or "\n" in text or "\r" in text:
        return ()
    prefix = text.casefold()
    if prefix in _SUBCOMMAND_SUGGESTIONS:
        return _SUBCOMMAND_SUGGESTIONS[prefix]
    if " " in prefix:
        return ()
    return tuple(
        item
        for item in _TOP_LEVEL_SUGGESTIONS
        if item.value.casefold().startswith(prefix)
    )


def parse_command(text: str) -> ProductCommand | None:
    """Return a typed slash command or None for an ordinary agent task."""

    if type(text) is not str:
        raise TypeError("command text must be a string")
    stripped = text.strip()
    if not stripped.startswith("/"):
        return None
    token, separator, rest = stripped.partition(" ")
    command = token.casefold()
    argument = rest.strip() if separator else ""

    if command == "/new" and not argument:
        return ProductCommand(CommandName.SESSION, CommandAction.NEW)
    if command == "/rename" and argument:
        return ProductCommand(CommandName.SESSION, CommandAction.RENAME, argument)
    if command == "/delete" and not argument:
        return ProductCommand(CommandName.SESSION, CommandAction.DELETE)
    if command == "/sessions" and not argument:
        return ProductCommand(CommandName.SESSION, CommandAction.LIST)
    if command == "/session":
        action, value = _subcommand(argument)
        if action == "search" and value:
            return ProductCommand(CommandName.SESSION, CommandAction.SEARCH, value)
        if argument and action != "search":
            return ProductCommand(CommandName.SESSION, CommandAction.SWITCH, argument)
    if command == "/memory":
        return _management_command(CommandName.MEMORY, argument, {
            "add": CommandAction.ADD,
            "delete": CommandAction.DELETE,
            "clear": CommandAction.CLEAR,
        })
    if command == "/skills" and not argument:
        return ProductCommand(CommandName.SKILL, CommandAction.LIST)
    if command == "/skill":
        return _management_command(CommandName.SKILL, argument, {
            "use": CommandAction.USE,
            "off": CommandAction.OFF,
            "clear": CommandAction.CLEAR,
        }, list_when_empty=False)
    if command == "/plugins" and not argument:
        return ProductCommand(CommandName.PLUGIN, CommandAction.LIST)
    if command == "/plugin":
        return _management_command(CommandName.PLUGIN, argument, {
            "enable": CommandAction.ENABLE,
            "disable": CommandAction.DISABLE,
        }, list_when_empty=False)
    if command == "/recall" and argument:
        return ProductCommand(CommandName.RECALL, CommandAction.SEARCH, argument)
    if command == "/help" and not argument:
        return ProductCommand(CommandName.HELP, CommandAction.SHOW)
    raise CommandError("COMMAND_INVALID", "unknown command or invalid arguments; use /help")


def _management_command(
    name: CommandName,
    argument: str,
    actions: dict[str, CommandAction],
    *,
    list_when_empty: bool = True,
) -> ProductCommand:
    if not argument and list_when_empty:
        return ProductCommand(name, CommandAction.LIST)
    action, value = _subcommand(argument)
    mapped = actions.get(action)
    if mapped is None:
        raise CommandError("COMMAND_INVALID", "unknown command or invalid arguments; use /help")
    if mapped is CommandAction.CLEAR:
        if value:
            raise CommandError("COMMAND_INVALID", "unknown command or invalid arguments; use /help")
        return ProductCommand(name, mapped)
    if not value:
        raise CommandError("COMMAND_INVALID", "unknown command or invalid arguments; use /help")
    return ProductCommand(name, mapped, value)


def _subcommand(argument: str) -> tuple[str, str]:
    action, separator, value = argument.partition(" ")
    return action.casefold(), value.strip() if separator else ""

"""Stable application-layer contracts for product front ends."""

from .commands import (
    CommandAction,
    CommandError,
    CommandHelp,
    CommandName,
    CommandSuggestion,
    ProductCommand,
    command_help,
    command_suggestions,
    parse_command,
)
from .events import ActivitySource, ActivityStatus, ProductEvent, ProductEventKind
from .service import CodingAgentService
from .state import ProductSnapshot, ProductStatus

__all__ = [
    "ActivityStatus",
    "ActivitySource",
    "CommandAction",
    "CommandError",
    "CommandHelp",
    "CommandName",
    "CommandSuggestion",
    "CodingAgentService",
    "ProductCommand",
    "ProductEvent",
    "ProductEventKind",
    "ProductSnapshot",
    "ProductStatus",
    "command_help",
    "command_suggestions",
    "parse_command",
]

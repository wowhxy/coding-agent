"""Stable application-layer contracts for product front ends."""

from .commands import (
    CommandAction,
    CommandError,
    CommandHelp,
    CommandName,
    ProductCommand,
    command_help,
    parse_command,
)
from .events import ActivityStatus, ProductEvent, ProductEventKind
from .service import CodingAgentService
from .state import ProductSnapshot, ProductStatus

__all__ = [
    "ActivityStatus",
    "CommandAction",
    "CommandError",
    "CommandHelp",
    "CommandName",
    "CodingAgentService",
    "ProductCommand",
    "ProductEvent",
    "ProductEventKind",
    "ProductSnapshot",
    "ProductStatus",
    "command_help",
    "parse_command",
]

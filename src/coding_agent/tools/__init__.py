"""Local tool registration, execution, and default composition."""

from __future__ import annotations

from ..config import RuntimeConfig
from .command import create_execute_command_tool
from .files import (
    create_list_files_tool,
    create_read_file_tool,
    create_replace_in_file_tool,
    create_search_text_tool,
    create_write_file_tool,
)
from .paths import WorkspacePaths
from .registry import ToolRegistry


def build_default_registry(config: RuntimeConfig) -> ToolRegistry:
    """Register the six documented local tools in stable order."""

    paths = WorkspacePaths(config.workspace)
    registry = ToolRegistry()
    registry.register(create_list_files_tool(paths))
    registry.register(create_search_text_tool(paths))
    registry.register(create_read_file_tool(paths))
    registry.register(create_write_file_tool(paths))
    registry.register(create_replace_in_file_tool(paths))
    registry.register(
        create_execute_command_tool(
            config.workspace,
            sensitive_env_names=config.sensitive_env_names,
            default_timeout=config.command_timeout,
        )
    )
    return registry


__all__ = ["build_default_registry"]

"""Construction of the immutable read-only child ToolRegistry surface."""

from __future__ import annotations

from pathlib import Path

from ..tools.files import (
    create_list_files_tool,
    create_read_file_tool,
    create_search_text_tool,
)
from ..tools.paths import WorkspacePaths
from ..tools.registry import ToolRegistry


def build_read_only_registry(workspace: Path) -> ToolRegistry:
    """Build exactly the three existing workspace inspection tools."""

    paths = WorkspacePaths(workspace)
    registry = ToolRegistry()
    registry.register(create_list_files_tool(paths))
    registry.register(create_search_text_tool(paths))
    registry.register(create_read_file_tool(paths))
    return registry

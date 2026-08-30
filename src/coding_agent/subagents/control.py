"""Parent-only control tool for bounded synchronous Subagent delegation."""

from __future__ import annotations

import json
from typing import Any

from ..protocol import ToolDefinition, ToolResult
from ..tools.registry import (
    RegisteredTool,
    ToolArgumentError,
    require_keys,
)
from .manager import SubagentManager
from .models import (
    MAX_DELEGATED_TASK_CHARS,
    SubagentContextMode,
    SubagentLimitError,
    SubagentRequest,
    SubagentRole,
)


def create_delegate_tasks_tool(manager: SubagentManager) -> RegisteredTool:
    """Create the parent control surface for one concurrent read-only batch."""

    definition = ToolDefinition(
        name="delegate_tasks",
        description=(
            "Delegate 1 to 3 independent read-only exploration, analysis, or "
            "review tasks and wait for their bounded results."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "tasks": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": manager.limits.max_subagent_tasks_per_batch,
                    "items": {
                        "type": "object",
                        "properties": {
                            "task": {
                                "type": "string",
                                "minLength": 1,
                                "maxLength": MAX_DELEGATED_TASK_CHARS,
                            },
                            "role": {
                                "type": "string",
                                "enum": [role.value for role in SubagentRole],
                                "default": SubagentRole.EXPLORE.value,
                            },
                            "context_mode": {
                                "type": "string",
                                "enum": [mode.value for mode in SubagentContextMode],
                                "default": SubagentContextMode.FRESH.value,
                            },
                        },
                        "required": ["task"],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["tasks"],
            "additionalProperties": False,
        },
    )

    def validate(arguments: dict[str, Any]) -> dict[str, Any]:
        require_keys(arguments, required=("tasks",))
        raw_tasks = arguments["tasks"]
        if type(raw_tasks) is not list:
            raise ToolArgumentError("tasks must be an array")
        if not 1 <= len(raw_tasks) <= manager.limits.max_subagent_tasks_per_batch:
            raise ToolArgumentError(
                "tasks must contain between 1 and "
                f"{manager.limits.max_subagent_tasks_per_batch} items"
            )

        requests: list[SubagentRequest] = []
        for index, raw in enumerate(raw_tasks):
            if type(raw) is not dict:
                raise ToolArgumentError(f"tasks[{index}] must be an object")
            require_keys(
                raw,
                required=("task",),
                optional=("role", "context_mode"),
            )
            try:
                role = SubagentRole(raw.get("role", SubagentRole.EXPLORE.value))
                mode = SubagentContextMode(
                    raw.get("context_mode", SubagentContextMode.FRESH.value)
                )
                requests.append(SubagentRequest(raw["task"], role, mode))
            except (TypeError, ValueError) as exc:
                raise ToolArgumentError(
                    f"tasks[{index}] is invalid: {str(exc)}"
                ) from None
        return {"requests": tuple(requests)}

    def handle(call_id: str, arguments: dict[str, Any]) -> ToolResult:
        try:
            results = manager.delegate(arguments["requests"])
        except SubagentLimitError as exc:
            return ToolResult(
                call_id,
                "delegate_tasks",
                False,
                "",
                exc.code,
                exc.message,
            )
        except Exception:
            return ToolResult(
                call_id,
                "delegate_tasks",
                False,
                "",
                "SUBAGENT_INTERNAL_ERROR",
                "subagent delegation failed unexpectedly",
            )

        output = json.dumps(
            {
                "results": [
                    {
                        "task_id": result.task_id,
                        "role": result.role.value,
                        "status": result.status.value,
                        "result": result.result,
                        "steps": result.steps,
                        "error": result.error,
                    }
                    for result in results
                ]
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return ToolResult(call_id, "delegate_tasks", True, output)

    return RegisteredTool(definition, validate, handle, "control")

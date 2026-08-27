"""Explicit, deterministic coding-agent loop."""

from __future__ import annotations

from collections.abc import Callable

from .context import ContextManager, ConversationHistory
from .model import ModelClient, ModelClientError
from .protocol import (
    AgentEvent,
    Message,
    Role,
    RunResult,
    RunStatus,
    ToolResult,
)
from .tools.registry import ToolRegistry


EventSink = Callable[[AgentEvent], None]
FailureFingerprint = tuple[str, str, str | None, str | None, str]


class AgentRunner:
    """Drive model turns and local tools until a protocol-level outcome."""

    def __init__(
        self,
        model_client: ModelClient,
        registry: ToolRegistry,
        context_manager: ContextManager,
        max_steps: int = 20,
        event_sink: EventSink | None = None,
    ) -> None:
        if max_steps <= 0:
            raise ValueError("max_steps must be positive")
        self.model_client = model_client
        self.registry = registry
        self.context_manager = context_manager
        self.max_steps = max_steps
        self.event_sink = event_sink

    def run(self, system_prompt: str, original_user_task: str) -> RunResult:
        """Run one task, distinguishing protocol termination from correctness."""

        step = 0
        try:
            history = ConversationHistory(system_prompt, original_user_task)
            last_failure: FailureFingerprint | None = None
            consecutive_failures = 0

            for step in range(1, self.max_steps + 1):
                messages = self.context_manager.build(history)
                model_turn = self.model_client.complete(
                    messages,
                    self.registry.definitions(),
                )

                if model_turn.tool_calls:
                    history.append(
                        Message(
                            Role.ASSISTANT,
                            model_turn.final_text,
                            model_turn.tool_calls,
                        )
                    )
                    for call in model_turn.tool_calls:
                        self._emit("tool_requested", step, call.name)
                        result = self.registry.dispatch(call)
                        result = self.context_manager.prepare_tool_result(result)
                        history.append(
                            Message(
                                Role.TOOL,
                                result.as_message_content(),
                                tool_call_id=call.id,
                            )
                        )
                        self._emit(
                            "tool_result",
                            step,
                            _tool_result_event_message(result),
                        )

                        if result.ok:
                            last_failure = None
                            consecutive_failures = 0
                        else:
                            fingerprint = (
                                call.name,
                                call.arguments_json,
                                result.error_code,
                                result.error_message,
                                result.output,
                            )
                            if fingerprint == last_failure:
                                consecutive_failures += 1
                            else:
                                last_failure = fingerprint
                                consecutive_failures = 1

                            if consecutive_failures >= 3:
                                return self._finish(
                                    RunStatus.STALLED,
                                    None,
                                    step,
                                    "three consecutive identical tool failures",
                                )
                    continue

                if model_turn.final_text and model_turn.final_text.strip():
                    return self._finish(
                        RunStatus.FINAL_RESPONSE,
                        model_turn.final_text,
                        step,
                        None,
                    )

                return self._finish(
                    RunStatus.MODEL_ERROR,
                    None,
                    step,
                    "model response contained neither tool calls nor non-empty final text",
                )

            return self._finish(
                RunStatus.MAX_STEPS,
                None,
                self.max_steps,
                "maximum step limit reached",
            )
        except ModelClientError as exc:
            message = str(exc).strip() or type(exc).__name__
            return self._finish(RunStatus.MODEL_ERROR, None, step, message)
        except Exception as exc:
            return RunResult(
                status=RunStatus.INTERNAL_ERROR,
                final_text=None,
                steps=step,
                error=f"unexpected internal error: {type(exc).__name__}",
            )

    def _emit(self, kind: str, step: int, message: str) -> None:
        if self.event_sink is not None:
            self.event_sink(AgentEvent(kind, step, message))

    def _finish(
        self,
        status: RunStatus,
        final_text: str | None,
        steps: int,
        error: str | None,
    ) -> RunResult:
        self._emit("run_finished", steps, status.value)
        return RunResult(status, final_text, steps, error)


def _tool_result_event_message(result: ToolResult) -> str:
    if result.ok:
        return f"{result.tool_name}: ok"
    return f"{result.tool_name}: error {result.error_code}"

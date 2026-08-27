"""Command-line composition and observable protocol-level outcomes."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Mapping, Sequence

from .agent import AgentRunner
from .config import ConfigError, RuntimeConfig, resolve_config
from .context import ContextManager
from .model import ModelClient
from .protocol import AgentEvent, RunResult, RunStatus
from .providers.openai_compatible import OpenAICompatibleClient
from .system_prompt import SYSTEM_PROMPT
from .tools import build_default_registry


ClientFactory = Callable[[str, str, str], ModelClient]
EXIT_CODES = {
    RunStatus.FINAL_RESPONSE: 0,
    RunStatus.MAX_STEPS: 3,
    RunStatus.STALLED: 4,
    RunStatus.MODEL_ERROR: 5,
    RunStatus.INTERNAL_ERROR: 6,
}


def main(
    argv: Sequence[str] | None = None,
    environ: Mapping[str, str] | None = None,
    client_factory: ClientFactory = OpenAICompatibleClient,
) -> int:
    """Run one coding task and return a stable process exit code."""

    parser = _build_parser()
    parsed_argv = list(sys.argv[1:] if argv is None else argv)
    if any(
        argument == "--api-key" or argument.startswith("--api-key=")
        for argument in parsed_argv
    ):
        print(
            "[error] raw API-key arguments are not supported; "
            "use --api-key-env NAME",
            file=sys.stderr,
        )
        return 2
    try:
        arguments = parser.parse_args(parsed_argv)
    except SystemExit as exc:
        return int(exc.code)

    try:
        config = resolve_config(
            workspace=arguments.workspace,
            base_url=arguments.base_url,
            model=arguments.model,
            api_key_env=arguments.api_key_env,
            max_steps=arguments.max_steps,
            max_context_chars=arguments.max_context_chars,
            recent_turns=arguments.recent_turns,
            max_tool_output_chars=arguments.max_tool_output_chars,
            command_timeout=arguments.command_timeout,
            environ=environ,
        )
    except ConfigError as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 2

    try:
        result = _run_agent(
            config,
            arguments.task,
            client_factory,
        )
    except Exception as exc:
        print(
            f"[error] unexpected internal error: {type(exc).__name__}",
            file=sys.stderr,
        )
        return 6

    _print_result(result, config.api_key)
    return EXIT_CODES[result.status]


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="coding-agent",
        description="Run one local coding-agent task.",
        allow_abbrev=False,
    )
    parser.add_argument("task", help="coding task for the agent")
    parser.add_argument(
        "--workspace",
        required=True,
        help="existing workspace directory used by all local tools",
    )
    parser.add_argument(
        "--base-url",
        help="OpenAI-compatible base URL (or CODING_AGENT_BASE_URL)",
    )
    parser.add_argument(
        "--model",
        help="model name (or CODING_AGENT_MODEL)",
    )
    parser.add_argument(
        "--api-key-env",
        default="OPENAI_API_KEY",
        metavar="NAME",
        help="environment-variable name containing the API key",
    )
    parser.add_argument(
        "--max-steps",
        type=int,
        default=20,
        help="maximum model steps (default: 20)",
    )
    parser.add_argument(
        "--max-context-chars",
        type=int,
        default=80_000,
        help="total deterministic context budget (default: 80000)",
    )
    parser.add_argument(
        "--recent-turns",
        type=int,
        default=8,
        help="recent complete turns retained (default: 8)",
    )
    parser.add_argument(
        "--max-tool-output-chars",
        type=int,
        default=20_000,
        help="per-tool output budget (default: 20000)",
    )
    parser.add_argument(
        "--command-timeout",
        type=int,
        default=30,
        help="default command timeout in seconds (default: 30)",
    )
    return parser


def _run_agent(
    config: RuntimeConfig,
    task: str,
    client_factory: ClientFactory,
) -> RunResult:
    client = client_factory(config.base_url, config.model, config.api_key)
    try:
        context_manager = ContextManager(
            max_context_chars=config.max_context_chars,
            recent_turns=config.recent_turns,
            max_tool_output_chars=config.max_tool_output_chars,
        )
        runner = AgentRunner(
            model_client=client,
            registry=build_default_registry(config),
            context_manager=context_manager,
            max_steps=config.max_steps,
            event_sink=_event_sink(config.api_key),
        )
        return runner.run(SYSTEM_PROMPT, task)
    finally:
        close = getattr(client, "close", None)
        if callable(close):
            close()


def _event_sink(api_key: str) -> Callable[[AgentEvent], None]:
    def emit(event: AgentEvent) -> None:
        message = _redact(event.message, api_key)
        if event.kind == "tool_requested":
            print(f"[step {event.step}] model requested: {message}")
        elif event.kind == "tool_result":
            print(f"[tool] {message}")

    return emit


def _print_result(result: RunResult, api_key: str) -> None:
    print(f"[final] protocol status: {result.status.value}")
    if result.final_text is not None:
        print("[response]")
        print(_redact(result.final_text, api_key))
    if result.error is not None:
        print(f"[error] {_redact(result.error, api_key)}", file=sys.stderr)


def _redact(text: str, api_key: str) -> str:
    if not api_key:
        return text
    return text.replace(api_key, "[REDACTED]")

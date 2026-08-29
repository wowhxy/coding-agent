"""Command-line composition and observable protocol-level outcomes."""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from .agent import AgentRunner
from .config import ConfigError, RuntimeConfig, resolve_config
from .context import ContextManager, ConversationHistory
from .context_policy import ContextPolicy
from .interactive import InteractiveSession
from .interactive_shell import InteractiveShell
from .model import ModelClient
from .memory import WorkspaceMemoryStore
from .memory_candidate import MemoryCandidateExtractor
from .protocol import AgentEvent, RunResult, RunStatus
from .plugins import PluginDiagnostic, PluginManager
from .recall import RecallService, should_automatic_recall
from .providers.openai_compatible import OpenAICompatibleClient
from .session import SessionError, SessionRecord
from .scheduler import BackgroundRuntime, BackgroundScheduler
from .session_store import JsonSessionStore, resolve_session_home
from .skill_selector import SkillActivator, SkillSelector
from .skills import SkillDiagnostic, SkillRegistry
from .summary import SummaryManager
from .subagents.control import create_delegate_tasks_tool
from .subagents.manager import SubagentManager
from .subagents.models import SubagentEvent
from .system_prompt import SYSTEM_PROMPT
from .tools import build_default_registry


ClientFactory = Callable[[str, str, str, str], ModelClient]
SecretReader = Callable[[str], str]
SessionStoreFactory = Callable[[Path], JsonSessionStore]
InputReader = Callable[[str], str]


@dataclass(frozen=True, slots=True)
class _ProviderPreset:
    base_url: str | None
    model: str | None
    api_key_env: str
    thinking_mode: str
    display_name: str


_PROVIDER_PRESETS = {
    "custom": _ProviderPreset(
        None,
        None,
        "OPENAI_API_KEY",
        "provider-default",
        "Provider",
    ),
    "deepseek": _ProviderPreset(
        "https://api.deepseek.com",
        "deepseek-v4-flash",
        "DEEPSEEK_API_KEY",
        "disabled",
        "DeepSeek",
    ),
    "openai": _ProviderPreset(
        "https://api.openai.com/v1",
        None,
        "OPENAI_API_KEY",
        "provider-default",
        "OpenAI",
    ),
}


def _context_policy(config: RuntimeConfig) -> ContextPolicy:
    return ContextPolicy(
        max_context_chars=config.max_context_chars,
        max_tool_output_chars=config.max_tool_output_chars,
        recent_turns=config.recent_turns,
        minimum_recent_turns=min(2, config.recent_turns),
    )


def _summary_manager(
    client: ModelClient, policy: ContextPolicy
) -> SummaryManager:
    return SummaryManager(
        client,
        threshold_chars=policy.summary_trigger_chars,
        recent_turns=policy.recent_turns,
        max_summary_chars=policy.summary_chars,
    )


def _subagent_manager(
    config: RuntimeConfig,
    client_factory: ClientFactory,
    policy: ContextPolicy,
    *,
    event_sink: Callable[[SubagentEvent], None] | None,
) -> SubagentManager:
    """Compose a lazy child factory without sharing the parent client."""

    return SubagentManager(
        config.workspace,
        lambda: client_factory(
            config.base_url,
            config.model,
            config.api_key,
            config.thinking_mode,
        ),
        lambda: ContextManager(policy=policy),
        sensitive_values=(config.api_key,),
        event_sink=event_sink,
    )


_RESERVED_API_KEY_ENV_NAMES = {
    name.casefold(): name
    for name in (
        "CODING_AGENT_BASE_URL",
        "CODING_AGENT_MODEL",
        "CODING_AGENT_SENSITIVE_ENV_NAMES",
        "CODING_AGENT_HOME",
        "LOCALAPPDATA",
        "XDG_DATA_HOME",
    )
}
EXIT_CODES = {
    RunStatus.FINAL_RESPONSE: 0,
    RunStatus.MAX_STEPS: 3,
    RunStatus.STALLED: 4,
    RunStatus.MODEL_ERROR: 5,
    RunStatus.INTERNAL_ERROR: 6,
    RunStatus.CANCELLED: 8,
}


def main(
    argv: Sequence[str] | None = None,
    environ: Mapping[str, str] | None = None,
    client_factory: ClientFactory = OpenAICompatibleClient,
    secret_reader: SecretReader | None = None,
    session_store_factory: SessionStoreFactory = JsonSessionStore,
    input_reader: InputReader = input,
) -> int:
    """Run one coding task or an interactive session."""

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
        if arguments.task is not None and (
            arguments.new_session
            or arguments.resume_session is not None
        ):
            parser.error(
                "--new-session and --resume-session are only valid "
                "in interactive mode"
            )
    except SystemExit as exc:
        return int(exc.code)

    preset = _PROVIDER_PRESETS[arguments.provider]
    api_key_env = (arguments.api_key_env or preset.api_key_env).strip()
    reserved_name = _RESERVED_API_KEY_ENV_NAMES.get(
        api_key_env.casefold()
    )
    if reserved_name is not None:
        print(
            "[error] API key environment variable "
            f"{reserved_name} is reserved",
            file=sys.stderr,
        )
        return 2

    runtime_environment = dict(os.environ if environ is None else environ)
    if not runtime_environment.get(api_key_env):
        reader = secret_reader
        if reader is None and sys.stdin.isatty():
            reader = getpass.getpass
        if reader is not None:
            api_key = reader(f"{preset.display_name} API Key (input hidden): ")
            if api_key:
                runtime_environment[api_key_env] = api_key

    try:
        config = resolve_config(
            workspace=arguments.workspace,
            base_url=_prefer_explicit(arguments.base_url, preset.base_url),
            model=_prefer_explicit(arguments.model, preset.model),
            api_key_env=api_key_env,
            thinking_mode=(
                arguments.thinking_mode or preset.thinking_mode
            ),
            max_steps=arguments.max_steps,
            max_context_chars=arguments.max_context_chars,
            recent_turns=arguments.recent_turns,
            max_tool_output_chars=arguments.max_tool_output_chars,
            command_timeout=arguments.command_timeout,
            environ=runtime_environment,
        )
    except ConfigError as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 2

    if arguments.task is not None:
        try:
            result = _run_agent(
                config,
                arguments.task,
                client_factory,
                arguments.provider,
                resolve_session_home(runtime_environment),
            )
        except SessionError as error:
            _print_session_error(error, config.api_key)
            return 7
        except Exception as exc:
            print(
                f"[error] unexpected internal error: {type(exc).__name__}",
                file=sys.stderr,
            )
            return 6

        _print_result(result, config.api_key)
        return EXIT_CODES[result.status]

    try:
        return _run_interactive(
            config=config,
            provider_name=arguments.provider,
            new_session=arguments.new_session,
            resume_session=arguments.resume_session,
            runtime_environment=runtime_environment,
            client_factory=client_factory,
            session_store_factory=session_store_factory,
            input_reader=input_reader,
        )
    except Exception as exc:
        print(
            f"[error] unexpected internal error: {type(exc).__name__}",
            file=sys.stderr,
        )
        return 6


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="coding-agent",
        description=(
            "Run one local coding-agent task, or start an interactive "
            "session when task is omitted."
        ),
        allow_abbrev=False,
    )
    parser.add_argument(
        "task",
        nargs="?",
        help="coding task for one-shot mode (omit for interactive mode)",
    )
    session_group = parser.add_mutually_exclusive_group()
    session_group.add_argument(
        "--new-session",
        action="store_true",
        help="create a new interactive session",
    )
    session_group.add_argument(
        "--resume-session",
        metavar="SESSION_ID",
        help="resume an interactive session by ID",
    )
    parser.add_argument(
        "--workspace",
        default=".",
        help=(
            "existing workspace directory used by all local tools "
            "(default: current directory)"
        ),
    )
    parser.add_argument(
        "--provider",
        choices=tuple(_PROVIDER_PRESETS),
        default="custom",
        help="provider defaults to apply (default: custom)",
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
        default=None,
        metavar="NAME",
        help="environment-variable name containing the API key",
    )
    parser.add_argument(
        "--thinking-mode",
        choices=("provider-default", "disabled"),
        default=None,
        help=(
            "override provider thinking mode "
            "(default: provider preset)"
        ),
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
    provider_name: str,
    memory_root: Path,
) -> RunResult:
    client = client_factory(
        config.base_url,
        config.model,
        config.api_key,
        config.thinking_mode,
    )
    print(f"[run] workspace: {config.workspace}")
    print(f"[run] provider: {provider_name}; model: {config.model}")
    plugin_manager: PluginManager | None = None
    try:
        policy = _context_policy(config)
        context_manager = ContextManager(policy=policy)
        memory_items = WorkspaceMemoryStore(
            memory_root
        ).context_items_for_context(config.workspace)
        context_manager.set_workspace_memories(memory_items)
        subagent_manager = _subagent_manager(
            config,
            client_factory,
            policy,
            event_sink=_subagent_event_sink(config.api_key),
        )
        subagent_manager.set_workspace_memories(memory_items)
        skill_registry = SkillRegistry(memory_root, config.workspace)
        skill_registry.discover()
        _print_skill_diagnostics(skill_registry.diagnostics, config.api_key)
        registry = build_default_registry(config)
        registry.register_many(
            (create_delegate_tasks_tool(subagent_manager),),
            source="control:subagent",
        )
        plugin_manager = PluginManager(memory_root, config.workspace, registry)
        plugin_manager.restore_enabled()
        _print_plugin_diagnostics(plugin_manager.diagnostics, config.api_key)
        runner = AgentRunner(
            model_client=client,
            registry=registry,
            context_manager=context_manager,
            max_steps=config.max_steps,
            event_sink=_event_sink(config.api_key),
            text_sink=_stream_sink(config.api_key),
            summary_manager=_summary_manager(client, policy),
            run_start_hook=subagent_manager.begin_parent_run,
            context_snapshot_sink=subagent_manager.observe_parent_context,
        )
        activation = SkillActivator(
            skill_registry, SkillSelector(client)
        ).prepare(task)
        runner.set_active_skills(activation.skills)
        subagent_manager.set_active_skills(activation.skills)
        _print_skill_diagnostics(activation.diagnostics, config.api_key)
        return runner.run(SYSTEM_PROMPT, task)
    finally:
        if plugin_manager is not None:
            plugin_manager.close()
        close = getattr(client, "close", None)
        if callable(close):
            close()


def _run_interactive(
    *,
    config: RuntimeConfig,
    provider_name: str,
    new_session: bool,
    resume_session: str | None,
    runtime_environment: Mapping[str, str],
    client_factory: ClientFactory,
    session_store_factory: SessionStoreFactory,
    input_reader: InputReader,
) -> int:
    try:
        session_home = resolve_session_home(runtime_environment)
        store = session_store_factory(session_home)
        memory_store = WorkspaceMemoryStore(session_home)
        skill_registry = SkillRegistry(session_home, config.workspace)
        skill_registry.discover()
        record, selection = _select_session(
            store,
            config,
            provider_name,
            new_session,
            resume_session,
        )
        history = (
            ConversationHistory.from_persisted(
                SYSTEM_PROMPT,
                record.messages,
            )
            if record.messages
            else ConversationHistory(SYSTEM_PROMPT)
        )
        memory_items = memory_store.context_items_for_context(config.workspace)
    except SessionError as error:
        _print_session_error(error, config.api_key)
        return 7

    _print_session_warnings(record, provider_name, config)
    _print_skill_diagnostics(skill_registry.diagnostics, config.api_key)

    client = client_factory(
        config.base_url,
        config.model,
        config.api_key,
        config.thinking_mode,
    )
    scheduler: BackgroundScheduler | None = None
    plugin_manager: PluginManager | None = None
    try:
        print(f"[run] workspace: {config.workspace}")
        print(f"[run] provider: {provider_name}; model: {config.model}")
        print(f"[session] {selection}: {record.session_id}")
        print("[session] enter /exit or press Ctrl+C to save and exit")

        policy = _context_policy(config)
        context_manager = ContextManager(policy=policy)
        context_manager.set_workspace_memories(memory_items)
        subagent_manager = _subagent_manager(
            config,
            client_factory,
            policy,
            event_sink=_subagent_event_sink(config.api_key),
        )
        subagent_manager.set_workspace_memories(memory_items)
        registry = build_default_registry(config)
        registry.register_many(
            (create_delegate_tasks_tool(subagent_manager),),
            source="control:subagent",
        )
        plugin_manager = PluginManager(
            session_home, config.workspace, registry
        )
        plugin_manager.restore_enabled()
        _print_plugin_diagnostics(plugin_manager.diagnostics, config.api_key)

        runner = AgentRunner(
            model_client=client,
            registry=registry,
            context_manager=context_manager,
            max_steps=config.max_steps,
            event_sink=_event_sink(config.api_key),
            text_sink=_stream_sink(config.api_key),
            summary_manager=_summary_manager(client, policy),
            run_start_hook=subagent_manager.begin_parent_run,
            context_snapshot_sink=subagent_manager.observe_parent_context,
        )
        runner.restore_summary_state(record.summary)
        skill_activator = SkillActivator(skill_registry, SkillSelector(client))
        recall_service = RecallService(store)

        def background_runtime(
            enabled_plugin_names: tuple[str, ...]
        ) -> BackgroundRuntime:
            background_memory = memory_store.context_items_for_context(
                config.workspace
            )
            background_client = client_factory(
                config.base_url,
                config.model,
                config.api_key,
                config.thinking_mode,
            )
            background_context = ContextManager(policy=policy)
            close = getattr(background_client, "close", None)
            background_plugins: PluginManager | None = None
            try:
                background_context.set_workspace_memories(background_memory)
                background_subagents = _subagent_manager(
                    config,
                    client_factory,
                    policy,
                    event_sink=None,
                )
                background_subagents.set_workspace_memories(background_memory)
                background_registry = build_default_registry(config)
                background_registry.register_many(
                    (create_delegate_tasks_tool(background_subagents),),
                    source="control:subagent",
                )
                background_plugins = PluginManager(
                    session_home, config.workspace, background_registry
                )
                background_plugins.load_snapshot(enabled_plugin_names)
                _print_plugin_diagnostics(
                    background_plugins.diagnostics, config.api_key
                )
                background_runner = AgentRunner(
                    model_client=background_client,
                    registry=background_registry,
                    context_manager=background_context,
                    max_steps=config.max_steps,
                    event_sink=None,
                    text_sink=None,
                    summary_manager=_summary_manager(background_client, policy),
                    run_start_hook=background_subagents.begin_parent_run,
                    context_snapshot_sink=(
                        background_subagents.observe_parent_context
                    ),
                )

                def prepare_background_skills(
                    task: str, manual_names: tuple[str, ...]
                ) -> tuple[SkillDiagnostic, ...]:
                    activation = SkillActivator(
                        skill_registry, SkillSelector(background_client)
                    ).prepare(task, manual_names)
                    background_runner.set_active_skills(activation.skills)
                    background_subagents.set_active_skills(activation.skills)
                    if should_automatic_recall(task):
                        background_runner.set_recalled_history(
                            recall_service.search(config.workspace, task)
                        )
                    return activation.diagnostics

                def close_background_runtime() -> None:
                    assert background_plugins is not None
                    background_plugins.close()
                    if callable(close):
                        close()

                return BackgroundRuntime(
                    background_runner,
                    close_background_runtime,
                    prepare_background_skills,
                )
            except Exception:
                if background_plugins is not None:
                    background_plugins.close()
                if callable(close):
                    close()
                raise

        scheduler = BackgroundScheduler(store, background_runtime)
        interactive = InteractiveSession(
            runner=runner,
            history=history,
            record=record,
            store=store,
            provider=provider_name,
            model=config.model,
            sensitive_values=(config.api_key,),
            input_reader=input_reader,
            output=lambda message: print(message, file=sys.stderr),
            result_sink=lambda result: _print_interactive_result(
                result,
                config.api_key,
            ),
        )
        shell = InteractiveShell(
            session=interactive,
            store=store,
            input_reader=input_reader,
            output=lambda message: print(message, file=sys.stderr),
            memory_store=memory_store,
            scheduler=scheduler,
            candidate_extractor=MemoryCandidateExtractor(client),
            skill_registry=skill_registry,
            skill_activator=skill_activator,
            recall_service=recall_service,
            plugin_manager=plugin_manager,
            subagent_manager=subagent_manager,
        )
        return shell.run()
    finally:
        if scheduler is not None:
            scheduler.shutdown()
        if plugin_manager is not None:
            plugin_manager.close()
        close = getattr(client, "close", None)
        if callable(close):
            close()


def _select_session(
    store: JsonSessionStore,
    config: RuntimeConfig,
    provider_name: str,
    new_session: bool,
    resume_session: str | None,
) -> tuple[SessionRecord, str]:
    if new_session:
        return (
            store.create_session(
                config.workspace,
                provider_name,
                config.model,
            ),
            "created",
        )
    if resume_session is not None:
        return (
            store.load_session(resume_session, config.workspace),
            "resumed",
        )
    latest = store.load_latest(config.workspace)
    if latest is not None:
        return latest, "resumed"
    return (
        store.create_session(
            config.workspace,
            provider_name,
            config.model,
        ),
        "created",
    )


def _print_session_warnings(
    record: SessionRecord,
    provider_name: str,
    config: RuntimeConfig,
) -> None:
    if record.provider != provider_name:
        print(
            "[warning] session provider changed: "
            f"{_redact(record.provider, config.api_key)} -> "
            f"{_redact(provider_name, config.api_key)}",
            file=sys.stderr,
        )
    if record.model != config.model:
        print(
            "[warning] session model changed: "
            f"{_redact(record.model, config.api_key)} -> "
            f"{_redact(config.model, config.api_key)}",
            file=sys.stderr,
        )


def _print_session_error(error: SessionError, api_key: str) -> None:
    print(
        f"[error] {error.error_code}: {_redact(error.message, api_key)}",
        file=sys.stderr,
    )


def _print_skill_diagnostics(
    diagnostics: tuple[SkillDiagnostic, ...], api_key: str
) -> None:
    for diagnostic in diagnostics:
        print(
            f"[skill warning] {diagnostic.code}: "
            f"{_redact(diagnostic.message, api_key)}",
            file=sys.stderr,
        )


def _print_plugin_diagnostics(
    diagnostics: tuple[PluginDiagnostic, ...], api_key: str
) -> None:
    for diagnostic in diagnostics:
        print(
            f"[plugin warning] {diagnostic.code}: "
            f"{_redact(diagnostic.message, api_key)}",
            file=sys.stderr,
        )


def _event_sink(api_key: str) -> Callable[[AgentEvent], None]:
    def emit(event: AgentEvent) -> None:
        message = _redact(event.message, api_key)
        if event.kind == "tool_requested":
            print(f"[step {event.step}] model requested: {message}")
        elif event.kind == "tool_result":
            print(f"[tool] {message}")

    return emit


def _subagent_event_sink(api_key: str) -> Callable[[SubagentEvent], None]:
    def emit(event: SubagentEvent) -> None:
        if event.kind == "batch_started":
            print(f"[subagents] batch started: {_redact(event.message, api_key)}")
        elif event.kind == "task_started":
            assert event.task_id is not None and event.role is not None
            print(f"[subagent {event.task_id}] running: {event.role.value}")
        elif event.kind == "task_completed":
            assert event.task_id is not None and event.status is not None
            print(f"[subagent {event.task_id}] completed: {event.status.value}")
        elif event.kind == "batch_collected":
            print(f"[subagents] collected: {_redact(event.message, api_key)}")

    return emit


def _print_result(result: RunResult, api_key: str) -> None:
    if result.streamed:
        print()
    print(f"[final] protocol status: {result.status.value}")
    if result.final_text is not None and not result.streamed:
        print("[response]")
        print(_redact(result.final_text, api_key))
    if result.error is not None:
        print(f"[error] {_redact(result.error, api_key)}", file=sys.stderr)


def _print_interactive_result(result: RunResult, api_key: str) -> None:
    if result.streamed:
        print()
    print(f"[final] protocol status: {result.status.value}")
    if result.final_text and not result.streamed:
        print(f"agent> {_redact(result.final_text, api_key)}")
    if result.error is not None:
        print(f"[error] {_redact(result.error, api_key)}", file=sys.stderr)


def _stream_sink(api_key: str) -> Callable[[str], None]:
    def emit(chunk: str) -> None:
        print(_redact(chunk, api_key), end="", flush=True)

    return emit


def _redact(text: str, api_key: str) -> str:
    if not api_key:
        return text
    return text.replace(api_key, "[REDACTED]")


def _prefer_explicit(
    explicit: str | None,
    preset: str | None,
) -> str | None:
    return explicit if explicit is not None else preset

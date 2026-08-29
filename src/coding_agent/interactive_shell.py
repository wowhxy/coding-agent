"""Lightweight interactive command shell around transactional sessions."""

from __future__ import annotations

from collections.abc import Callable

from .interactive import InteractiveSession, _redact_text
from .memory import WorkspaceMemoryStore
from .memory_candidate import MemoryCandidateExtractor, is_safe_candidate
from .protocol import Message, RunStatus
from .plugins import PluginError, PluginManager
from .recall import RecallEntry, RecallService, should_automatic_recall
from .session import SessionError
from .scheduler import BackgroundScheduler
from .session_store import JsonSessionStore, SessionSummary
from .skill_selector import SkillActivator
from .skills import ManualSkillState, SkillError, SkillRegistry


class InteractiveShell:
    """Dispatch local slash commands and ordinary agent turns."""

    def __init__(
        self,
        session: InteractiveSession,
        store: JsonSessionStore,
        input_reader: Callable[[str], str] = input,
        output: Callable[[str], None] = print,
        memory_store: WorkspaceMemoryStore | None = None,
        scheduler: BackgroundScheduler | None = None,
        candidate_extractor: MemoryCandidateExtractor | None = None,
        skill_registry: SkillRegistry | None = None,
        manual_skills: ManualSkillState | None = None,
        skill_activator: SkillActivator | None = None,
        recall_service: RecallService | None = None,
        plugin_manager: PluginManager | None = None,
    ) -> None:
        self.session = session
        self.store = store
        self.input_reader = input_reader
        self.output = output
        self.memory_store = memory_store
        self.scheduler = scheduler
        self.candidate_extractor = candidate_extractor
        self.skill_registry = skill_registry
        self.manual_skills = manual_skills or ManualSkillState()
        self.skill_activator = skill_activator
        self.recall_service = recall_service
        self.plugin_manager = plugin_manager
        self._pending_recall: tuple[RecallEntry, ...] = ()

    def run(self) -> int:
        while True:
            try:
                raw = self.input_reader("you> ")
            except (EOFError, KeyboardInterrupt):
                return 0

            stripped = raw.strip()
            if not stripped:
                continue
            parts = stripped.split(maxsplit=1)
            command = parts[0]
            argument = parts[1] if len(parts) == 2 else ""
            normalized = command.casefold()
            if normalized == "/exit" and not argument:
                return 0

            if not normalized.startswith("/"):
                if self.scheduler is not None:
                    if self.scheduler.is_busy(self.session.record.session_id):
                        self.output(
                            "[error] SESSION_BUSY: session already has a running task"
                        )
                        continue
                    self._refresh_active_session()
                try:
                    self._execute_task(raw, capture_candidates=True)
                except KeyboardInterrupt:
                    return 0
                except SessionError as error:
                    self._print_error(error)
                    return 7
                continue

            try:
                if normalized == "/new" and not argument:
                    self._new()
                elif normalized == "/rename":
                    self._require_idle()
                    self._rename(argument)
                elif normalized == "/delete" and not argument:
                    self._require_idle()
                    self._delete()
                elif normalized == "/sessions" and not argument:
                    self._show_sessions(self.store.list_sessions(self.session.record.workspace))
                elif normalized == "/search":
                    self._show_sessions(
                        self.store.search_sessions(self.session.record.workspace, argument)
                    )
                elif normalized == "/use":
                    self._use(argument)
                elif normalized == "/multiline" and not argument:
                    multiline = self._read_multiline()
                    if multiline is not None and multiline.strip():
                        try:
                            self._execute_task(multiline, capture_candidates=False)
                        except SessionError as error:
                            self._print_error(error)
                            return 7
                elif normalized == "/memory":
                    self._memory(argument)
                elif normalized == "/recall":
                    self._recall(argument)
                elif normalized == "/skills" and not argument:
                    self._skills()
                elif normalized == "/skill":
                    self._skill(argument)
                elif normalized == "/plugins" and not argument:
                    self._plugins()
                elif normalized == "/plugin":
                    self._plugin(argument)
                elif normalized == "/background":
                    self._background(argument)
                elif normalized == "/jobs" and not argument:
                    self._jobs()
                elif normalized == "/cancel":
                    self._cancel_job(argument)
                else:
                    self.output(
                        f"[error] unknown command: {command}; "
                        "use /sessions, /new, /memory, /recall, /skills, "
                        "/plugins, /jobs, or /exit"
                    )
            except KeyboardInterrupt:
                return 0
            except SessionError as error:
                self._print_error(error)
            except SkillError as error:
                self._print_skill_error(error)
            except PluginError as error:
                self.output(f"[error] {error.code}: {error.message}")

    def _print_error(self, error: SessionError) -> None:
        message = _redact_text(error.message, self.session.sensitive_values)
        self.output(f"[error] {error.error_code}: {message}")

    def _print_skill_error(self, error: SkillError) -> None:
        message = _redact_text(error.message, self.session.sensitive_values)
        self.output(f"[error] {error.error_code}: {message}")

    def _plugins(self) -> None:
        manager = self._require_plugin_manager()
        plugins = manager.discover()
        self.output("NAME VERSION STATUS DESCRIPTION")
        if not plugins:
            self.output("[plugin] none")
        for item in plugins:
            metadata = item.metadata
            description = _redact_text(
                metadata.description, self.session.sensitive_values
            )
            self.output(
                f"{metadata.name}  {metadata.version}  {item.status}  "
                f"{description}"
            )
        for diagnostic in manager.diagnostics:
            message = _redact_text(
                diagnostic.message, self.session.sensitive_values
            )
            self.output(
                f"[plugin warning] {diagnostic.code}: {message}"
            )

    def _plugin(self, argument: str) -> None:
        manager = self._require_plugin_manager()
        action, _, value = argument.strip().partition(" ")
        name = value.strip()
        if action.casefold() == "enable" and name:
            manager.enable(name)
            self.output(f"[plugin] enabled: {name}")
            return
        if action.casefold() == "disable" and name:
            manager.disable(name)
            self.output(f"[plugin] disabled: {name}")
            return
        raise PluginError(
            "PLUGIN_COMMAND_INVALID",
            "use /plugin enable <name> or /plugin disable <name>",
        )

    def _require_plugin_manager(self) -> PluginManager:
        if self.plugin_manager is None:
            raise PluginError(
                "PLUGIN_UNAVAILABLE", "plugin system is unavailable"
            )
        return self.plugin_manager

    def _new(self) -> None:
        current = self.session.record
        created = self.store.create_session(
            current.workspace, self.session.provider, self.session.model
        )
        self.session.activate(created)
        self._pending_recall = ()
        self.output(f"[session] created: {created.session_id}")

    def _rename(self, name: str) -> None:
        renamed = self.store.rename_session(self.session.record, name)
        self.session.record = renamed
        self.output(f"[session] renamed: {renamed.name}")

    def _delete(self) -> None:
        current = self.session.record
        try:
            selected = self.store.delete_session(current.session_id, current.workspace)
        except SessionError as error:
            if error.error_code != "SESSION_NOT_FOUND" or current.messages:
                raise
            selected = self.store.load_latest(current.workspace)
        if selected is None:
            selected = self.store.create_session(
                current.workspace, self.session.provider, self.session.model
            )
        self.manual_skills.remove_session(current.session_id)
        self.session.activate(selected)
        self._pending_recall = ()
        self.output(
            f"[session] deleted {current.session_id}; active: {selected.session_id}"
        )

    def _show_sessions(self, summaries: tuple[SessionSummary, ...]) -> None:
        if not summaries:
            self.output("[session] no matching sessions")
            return
        for item in summaries:
            marker = "*" if item.is_latest else " "
            name = item.name or "(unnamed)"
            self.output(
                f"{marker} {item.session_id}  "
                f"{item.updated_at.isoformat()}  {name}"
            )

    def _use(self, session_id: str) -> None:
        selected = self.store.load_session(
            session_id.strip(), self.session.record.workspace
        )
        self.session.activate(selected)
        self._pending_recall = ()
        self.output(f"[session] active: {selected.session_id}")

    def _read_multiline(self) -> str | None:
        lines: list[str] = []
        self.output("[input] multiline: /send to submit, /cancel to discard")
        while True:
            try:
                line = self.input_reader("... ")
            except (EOFError, KeyboardInterrupt):
                self.output("[input] multiline cancelled")
                return None
            command = line.strip().casefold()
            if command == "/cancel":
                self.output("[input] multiline cancelled")
                return None
            if command == "/send":
                return "\n".join(lines)
            lines.append(line)

    def _execute_task(self, task: str, *, capture_candidates: bool) -> None:
        runner = self.session.runner
        set_active = getattr(runner, "set_active_skills", None)
        set_recall = getattr(runner, "set_recalled_history", None)
        before = len(self.session.history.messages)
        recall_entries = self._pending_recall
        self._pending_recall = ()
        if (
            not recall_entries
            and self.recall_service is not None
            and should_automatic_recall(task)
        ):
            try:
                recall_entries = self.recall_service.search(
                    self.session.record.workspace,
                    task,
                    exclude_session_id=self.session.record.session_id,
                )
            except SessionError:
                recall_entries = ()
        if callable(set_recall):
            set_recall(recall_entries)
        if self.skill_activator is not None:
            result = self.skill_activator.prepare(
                task,
                self.manual_skills.names(self.session.record.session_id),
            )
            if callable(set_active):
                set_active(result.skills)
            for diagnostic in result.diagnostics:
                self.output(
                    f"[skill warning] {diagnostic.code}: {diagnostic.message}"
                )
        try:
            run_result = self.session.execute(task)
            if capture_candidates and run_result.status is RunStatus.FINAL_RESPONSE:
                self._capture_candidates(self.session.history.messages[before:])
        finally:
            if callable(set_active):
                set_active(())
            if callable(set_recall):
                set_recall(())

    def _recall(self, query: str) -> None:
        if self.recall_service is None:
            raise SessionError("RECALL_UNAVAILABLE", "session recall is unavailable")
        entries = self.recall_service.search(
            self.session.record.workspace,
            query,
            exclude_session_id=self.session.record.session_id,
        )
        self._pending_recall = entries
        if not entries:
            self.output("[recall] no matches")
            return
        for item in entries:
            self.output(
                f"[recall] {item.session_id} {item.source} "
                f"#{item.ordinal}: {item.excerpt}"
            )

    def _skills(self) -> None:
        registry = self._require_skill_registry()
        metadata = registry.metadata
        active = set(self.manual_skills.names(self.session.record.session_id))
        if not metadata:
            self.output("[skill] none")
        for item in metadata:
            status = "active" if item.name in active else "inactive"
            self.output(f"[skill] {item.name}  {item.scope}  {status}")
        for diagnostic in registry.diagnostics:
            self.output(f"[skill warning] {diagnostic.code}: {diagnostic.message}")

    def _skill(self, argument: str) -> None:
        registry = self._require_skill_registry()
        action, _, value = argument.strip().partition(" ")
        normalized = action.casefold()
        name = value.strip()
        session_id = self.session.record.session_id
        if normalized == "use" and name:
            self.manual_skills.use(session_id, name, registry)
            self.output(f"[skill] active: {name}")
        elif normalized == "off" and name:
            self.manual_skills.off(session_id, name)
            self.output(f"[skill] inactive: {name}")
        elif normalized == "clear" and not name:
            self.manual_skills.clear(session_id)
            self.output("[skill] cleared")
        else:
            raise SkillError(
                "SKILL_COMMAND_INVALID",
                "Use /skill use <name>, /skill off <name>, or /skill clear.",
            )

    def _require_skill_registry(self) -> SkillRegistry:
        if self.skill_registry is None:
            raise SkillError("SKILL_UNAVAILABLE", "The Skill system is unavailable.")
        return self.skill_registry

    def _memory(self, argument: str) -> None:
        if self.memory_store is None:
            raise SessionError("MEMORY_UNAVAILABLE", "workspace memory is unavailable")
        workspace = self.session.record.workspace
        action, _, value = argument.strip().partition(" ")
        normalized = action.casefold()
        if not argument.strip():
            items = self.memory_store.list(workspace)
            if not items:
                self.output("[memory] empty")
            for item in items:
                self.output(
                    f"[memory] {item.id}: ({item.kind}) "
                    f"{item.key} = {item.content}"
                )
            return
        if normalized == "add":
            item = self.memory_store.add(
                workspace, value, self.session.sensitive_values
            )
            self.output(f"[memory] added: {item.id}")
        elif normalized == "delete":
            deleted = self.memory_store.delete(workspace, value.strip())
            self.output("[memory] deleted" if deleted else "[memory] not found")
        elif normalized == "clear" and not value:
            self.memory_store.clear(workspace)
            self.output("[memory] cleared")
        else:
            raise SessionError(
                "MEMORY_INVALID",
                "use /memory, /memory add <text>, /memory delete <id>, or /memory clear",
            )
        self._refresh_workspace_memory()

    def _capture_candidates(self, turn_messages: tuple[Message, ...]) -> None:
        if self.candidate_extractor is None or self.memory_store is None:
            return
        candidates = self.candidate_extractor.extract(turn_messages)
        for candidate in candidates:
            if not is_safe_candidate(candidate, self.session.sensitive_values):
                continue
            match = self.memory_store.match(
                self.session.record.workspace,
                candidate.content,
                candidate.kind,
                key=candidate.key,
            )
            if match.status in {"exact_duplicate", "normalized_duplicate"}:
                continue
            action = "replace" if match.status == "conflict" else "save"
            try:
                answer = self.input_reader(
                    f"[memory candidate] {action} ({candidate.kind}) "
                    f"{candidate.text} [y/N] "
                )
            except EOFError:
                return
            if answer.strip().casefold() != "y":
                continue
            try:
                if match.status == "conflict" and match.existing is not None:
                    item = self.memory_store.replace(
                        self.session.record.workspace,
                        match.existing.id,
                        candidate.content,
                        self.session.sensitive_values,
                        kind=candidate.kind,
                        source="confirmed_candidate",
                        key=candidate.key,
                    )
                else:
                    item = self.memory_store.add(
                        self.session.record.workspace,
                        candidate.content,
                        self.session.sensitive_values,
                        kind=candidate.kind,
                        source="confirmed_candidate",
                        key=candidate.key,
                    )
                self._refresh_workspace_memory()
                self.output(f"[memory] added: {item.id}")
            except SessionError as error:
                self._print_error(error)

    def _refresh_workspace_memory(self) -> None:
        if self.memory_store is None:
            return
        structured_setter = getattr(
            self.session.runner, "set_workspace_memories", None
        )
        if callable(structured_setter):
            structured_setter(
                self.memory_store.context_items_for_context(
                    self.session.record.workspace
                )
            )
            return
        self.session.runner.set_workspace_memory(
            self.memory_store.render(self.session.record.workspace)
        )

    def _background(self, task: str) -> None:
        if self.scheduler is None:
            raise SessionError("JOB_UNAVAILABLE", "background scheduler is unavailable")
        if not task.strip():
            raise SessionError("JOB_INVALID", "background task is required")
        persisted = self.store.save(self.session.record)
        self.session.record = persisted
        job = self.scheduler.submit(
            persisted,
            task,
            self.session.sensitive_values,
            self.manual_skills.names(persisted.session_id),
            (
                self.plugin_manager.enabled_names
                if self.plugin_manager is not None
                else ()
            ),
        )
        self.output(
            f"[job] started: {job.id}; session: {job.session_id}"
        )

    def _jobs(self) -> None:
        if self.scheduler is None:
            raise SessionError("JOB_UNAVAILABLE", "background scheduler is unavailable")
        jobs = self.scheduler.list()
        if not jobs:
            self.output("[job] none")
        for job in jobs:
            detail = f"; {job.error}" if job.error else ""
            self.output(
                f"[job] {job.id}  {job.status.value}  session={job.session_id}{detail}"
            )

    def _cancel_job(self, job_id: str) -> None:
        if self.scheduler is None:
            raise SessionError("JOB_UNAVAILABLE", "background scheduler is unavailable")
        if not self.scheduler.cancel(job_id.strip()):
            raise SessionError("JOB_NOT_FOUND", "running background job was not found")
        self.output(f"[job] cancellation requested: {job_id.strip()}")

    def _require_idle(self) -> None:
        if self.scheduler is not None and self.scheduler.is_busy(
            self.session.record.session_id
        ):
            raise SessionError("SESSION_BUSY", "session already has a running task")

    def _refresh_active_session(self) -> None:
        try:
            current = self.store.load_session(
                self.session.record.session_id, self.session.record.workspace
            )
        except SessionError as error:
            if error.error_code == "SESSION_NOT_FOUND" and not self.session.record.messages:
                return
            raise
        if current != self.session.record:
            self.session.activate(current)

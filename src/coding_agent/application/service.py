"""Synchronous product facade over the existing coding-agent core."""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from ..agent import AgentRunner
from ..config import RuntimeConfig
from ..context import ContextManager, ConversationHistory
from ..context_policy import ContextPolicy
from ..interactive import InteractiveSession
from ..memory import MemoryItem, WorkspaceMemoryStore
from ..memory_candidate import MemoryCandidate, MemoryCandidateExtractor, is_safe_candidate
from ..model import ModelClient
from ..plugins import PluginInfo, PluginManager
from ..protocol import AgentEvent, Message, Role, RunResult, RunStatus
from ..recall import RecallEntry, RecallService, should_automatic_recall
from ..session import SessionError, SessionRecord
from ..session_store import JsonSessionStore, SessionSummary
from ..skill_selector import SkillActivator, SkillSelector
from ..skills import ManualSkillState, SkillRegistry
from ..subagents.control import create_delegate_tasks_tool
from ..subagents.manager import SubagentManager
from ..subagents.models import SubagentEvent
from ..summary import SummaryManager
from ..system_prompt import SYSTEM_PROMPT
from ..tools import build_default_registry
from .changes import (
    activity_views,
    compare_snapshots,
    snapshot_workspace,
    verification_views,
)
from .events import (
    ActivitySource,
    ActivityStatus,
    ProductEvent,
    ProductEventKind,
    adapt_agent_event,
    adapt_subagent_event,
    redact_product_text,
)
from .state import (
    AgentState,
    ConversationItem,
    ConversationKind,
    MemoryCandidateView,
    MemoryView,
    PluginView,
    ProductSnapshot,
    ProductStatus,
    RecallView,
    SessionView,
    SkillView,
)


ClientFactory = Callable[[str, str, str, str], ModelClient]
EventSubscriber = Callable[[ProductEvent], None]
_PLUGIN_WARNING = "Executable plugins run as trusted local code."


@dataclass(slots=True)
class _PendingCandidate:
    view: MemoryCandidateView
    candidate: MemoryCandidate


class CodingAgentService:
    """Own one product runtime and expose stable UI-facing operations."""

    def __init__(
        self,
        config: RuntimeConfig,
        provider_name: str,
        session_home: Path,
        client_factory: ClientFactory,
        *,
        new_session: bool = False,
        resume_session: str | None = None,
    ) -> None:
        self.config = config
        self.provider_name = provider_name
        self.session_home = Path(session_home)
        self._client_factory = client_factory
        self._lock = threading.RLock()
        self._subscribers: list[EventSubscriber] = []
        self._cancel_event = threading.Event()
        self._agent_state = AgentState.READY
        self._running = False
        self._closed = False
        self._close_pending = False
        self._resources_closed = False
        self._current_task_id: str | None = None
        self._task_number = 0
        self._active_subagents = 0
        self._active_skill_names: tuple[str, ...] = ()
        self._session_results: dict[str, str] = {}
        self._changes = ()
        self._verifications = ()
        self._pending_recall: tuple[RecallEntry, ...] = ()
        self._pending_candidates: dict[str, _PendingCandidate] = {}
        self._candidate_number = 0

        self.store = JsonSessionStore(self.session_home)
        self.memory_store = WorkspaceMemoryStore(self.session_home)
        self.skill_registry = SkillRegistry(self.session_home, config.workspace)
        self.skill_registry.discover()
        self.manual_skills = ManualSkillState()
        self.recall_service = RecallService(self.store)
        self._client = client_factory(
            config.base_url, config.model, config.api_key, config.thinking_mode
        )
        self._plugin_manager: PluginManager | None = None
        try:
            record = self._select_session(new_session, resume_session)
            history = (
                ConversationHistory.from_persisted(SYSTEM_PROMPT, record.messages)
                if record.messages
                else ConversationHistory(SYSTEM_PROMPT)
            )
            policy = _context_policy(config)
            self._context_manager = ContextManager(policy=policy)
            memories = self.memory_store.context_items_for_context(config.workspace)
            self._context_manager.set_workspace_memories(memories)
            self._subagent_manager = SubagentManager(
                config.workspace,
                lambda: client_factory(
                    config.base_url,
                    config.model,
                    config.api_key,
                    config.thinking_mode,
                ),
                lambda: ContextManager(policy=policy),
                sensitive_values=(config.api_key,),
                event_sink=self._on_subagent_event,
            )
            self._subagent_manager.set_workspace_memories(memories)
            registry = build_default_registry(config)
            registry.register_many(
                (create_delegate_tasks_tool(self._subagent_manager),),
                source="control:subagent",
            )
            self._plugin_manager = PluginManager(
                self.session_home, config.workspace, registry
            )
            self._plugin_manager.restore_enabled()
            self._registry = registry
            self._runner = AgentRunner(
                self._client,
                registry,
                self._context_manager,
                max_steps=config.max_steps,
                event_sink=self._on_agent_event,
                text_sink=self._on_text_delta,
                summary_manager=SummaryManager(
                    self._client,
                    threshold_chars=policy.summary_trigger_chars,
                    recent_turns=policy.recent_turns,
                    max_summary_chars=policy.summary_chars,
                ),
                run_start_hook=self._subagent_manager.begin_parent_run,
                context_snapshot_sink=self._subagent_manager.observe_parent_context,
            )
            self._runner.restore_summary_state(record.summary)
            self._interactive = InteractiveSession(
                self._runner,
                history,
                record,
                self.store,
                provider_name,
                config.model,
                (config.api_key,),
            )
            self._skill_activator = SkillActivator(
                self.skill_registry, SkillSelector(self._client)
            )
            self._candidate_extractor = MemoryCandidateExtractor(self._client)
        except BaseException:
            if self._plugin_manager is not None:
                self._plugin_manager.close()
            _close_client(self._client)
            raise

    @classmethod
    def create(
        cls,
        config: RuntimeConfig,
        provider_name: str,
        session_home: Path,
        client_factory: ClientFactory,
        *,
        new_session: bool = False,
        resume_session: str | None = None,
    ) -> CodingAgentService:
        return cls(
            config,
            provider_name,
            session_home,
            client_factory,
            new_session=new_session,
            resume_session=resume_session,
        )

    def subscribe(self, sink: EventSubscriber) -> Callable[[], None]:
        if not callable(sink):
            raise TypeError("event subscriber must be callable")
        with self._lock:
            self._require_open()
            self._subscribers.append(sink)

        def unsubscribe() -> None:
            with self._lock:
                if sink in self._subscribers:
                    self._subscribers.remove(sink)

        return unsubscribe

    def submit_task(self, text: str) -> RunResult:
        if type(text) is not str or not text.strip():
            raise ValueError("task must not be empty")
        with self._lock:
            self._require_open()
            if self._running:
                raise ValueError("a task is already running")
            self._running = True
            self._agent_state = AgentState.RUNNING
            self._cancel_event.clear()
            self._task_number += 1
            task_id = f"task-{self._task_number}"
            self._current_task_id = task_id
            session_id = self._interactive.record.session_id
        before_message_count = len(self._interactive.history.messages)
        result: RunResult | None = None
        try:
            before_snapshot = snapshot_workspace(self.config.workspace)
            self._emit(ProductEventKind.TASK_STARTED, text, ActivityStatus.RUNNING)
            self._emit(
                ProductEventKind.MODEL_WAITING,
                "Waiting for provider",
                ActivityStatus.RUNNING,
            )
            self._prepare_turn(text)
            result = self._interactive.execute(
                text, cancel_check=self._cancel_event.is_set
            )
            if result.status is not RunStatus.CANCELLED:
                after_snapshot = snapshot_workspace(self.config.workspace)
                self._changes = compare_snapshots(
                    before_snapshot,
                    after_snapshot,
                    sensitive_values=(self.config.api_key,),
                )
                messages = self._interactive.history.messages
                self._verifications = verification_views(
                    messages, sensitive_values=(self.config.api_key,)
                )
                self._capture_candidates(
                    messages[before_message_count:], result.status
                )
                if self._changes:
                    self._emit(
                        ProductEventKind.FILE_CHANGES,
                        f"{len(self._changes)} changed file(s)",
                        ActivityStatus.SUCCEEDED,
                    )
                for verification in self._verifications:
                    self._emit(
                        ProductEventKind.VERIFICATION,
                        verification.summary,
                        ActivityStatus.SUCCEEDED
                        if verification.ok
                        else ActivityStatus.FAILED,
                        detail=verification.command,
                    )
            with self._lock:
                self._session_results[session_id] = result.status.value
            if result.status is RunStatus.FINAL_RESPONSE:
                self._emit(
                    ProductEventKind.FINAL_RESPONSE,
                    result.final_text or "",
                    ActivityStatus.SUCCEEDED,
                )
            elif result.status is RunStatus.CANCELLED:
                self._emit(
                    ProductEventKind.TASK_CANCELLED,
                    "Task cancelled; incomplete turn was not saved",
                    ActivityStatus.CANCELLED,
                )
            else:
                self._emit(
                    ProductEventKind.TASK_FAILED,
                    _failure_presentation(result),
                    ActivityStatus.FAILED,
                )
            return result
        except BaseException as exc:
            self._emit(
                ProductEventKind.ERROR,
                _safe_exception_message(exc),
                ActivityStatus.FAILED,
            )
            raise
        finally:
            self._runner.set_active_skills(())
            self._subagent_manager.set_active_skills(())
            self._runner.set_recalled_history(())
            close_pending = False
            with self._lock:
                self._active_skill_names = self.manual_skills.names(
                    self._interactive.record.session_id
                )
                self._running = False
                self._current_task_id = None
                self._active_subagents = 0
                close_pending = self._close_pending
                if not close_pending:
                    self._agent_state = AgentState.READY
            if close_pending:
                self._close_resources()

    def cancel_task(self) -> bool:
        with self._lock:
            if not self._running or self._closed:
                return False
            self._cancel_event.set()
            self._agent_state = AgentState.CANCELLING
        self._emit(
            ProductEventKind.STATE_CHANGED,
            "Cancellation requested",
            ActivityStatus.CANCELLED,
        )
        return True

    def snapshot(self) -> ProductSnapshot:
        status = self.get_status()
        return ProductSnapshot(
            status,
            self.list_sessions(),
            self.get_conversation(),
            activity_views(
                self._interactive.history.messages,
                sensitive_values=(self.config.api_key,),
                tool_observer=self._registry.historical_observation_for,
            ),
            self._changes,
            self._verifications,
        )

    def get_status(self) -> ProductStatus:
        """Return the cheap status projection without rebuilding conversation views."""

        with self._lock:
            state = self._agent_state
            active_subagents = self._active_subagents
            active_skills = self._active_skill_names
        report = self._context_manager.last_report
        status = ProductStatus(
            self.provider_name,
            self.config.model,
            self.config.workspace,
            self._interactive.record.session_id,
            state,
            min(report.final_context_chars, self.config.max_context_chars),
            self.config.max_context_chars,
            self._runner.summary_state is not None,
            len(self.memory_store.list(self.config.workspace)),
            active_skills,
            self._plugin_manager.enabled_names,
            active_subagents,
        )
        return status

    def get_conversation(self) -> tuple[ConversationItem, ...]:
        items: list[ConversationItem] = []
        for index, message in enumerate(self._interactive.history.messages):
            if message.role is Role.SYSTEM or not message.content:
                continue
            if message.role is Role.USER:
                kind = ConversationKind.USER
            elif message.role is Role.ASSISTANT:
                kind = ConversationKind.ASSISTANT
            else:
                continue
            items.append(
                ConversationItem(
                    f"message-{index}",
                    kind,
                    redact_product_text(message.content, (self.config.api_key,)),
                )
            )
        return tuple(items)

    def list_sessions(
        self, *, limit: int = 50, offset: int = 0
    ) -> tuple[SessionView, ...]:
        summaries = list(
            self.store.list_sessions(
                self.config.workspace, limit=limit, offset=offset
            )
        )
        current = self._interactive.record
        if all(item.session_id != current.session_id for item in summaries):
            summaries.insert(
                0,
                SessionSummary(
                    current.session_id, current.name, current.updated_at, False
                ),
            )
            summaries = summaries[:limit]
        return tuple(self._session_view(item) for item in summaries)

    def search_sessions(self, query: str) -> tuple[SessionView, ...]:
        return tuple(
            self._session_view(item)
            for item in self.store.search_sessions(self.config.workspace, query)
        )

    def new_session(self) -> SessionView:
        self._require_idle()
        current = self._interactive.record
        record = self.store.create_session(
            current.workspace, self.provider_name, self.config.model
        )
        self._activate_session(record)
        self._emit(ProductEventKind.SESSION_CHANGED, "New session")
        return self._session_view_record(record)

    def switch_session(self, session_id: str) -> SessionView:
        self._require_idle()
        record = self.store.load_session(session_id.strip(), self.config.workspace)
        record = self.store.save(record)
        self._activate_session(record)
        self._emit(ProductEventKind.SESSION_CHANGED, "Session switched")
        return self._session_view_record(record)

    def rename_session(
        self, name: str, session_id: str | None = None
    ) -> SessionView:
        with self._lock:
            self._require_open()
            current = self._interactive.record
        target_id = current.session_id if session_id is None else session_id.strip()
        if target_id == current.session_id:
            record = self._interactive.rename(name)
        else:
            record = self.store.load_session(target_id, current.workspace)
            record = self.store.rename_session(record, name, make_latest=False)
        self._emit(ProductEventKind.SESSION_CHANGED, "Session renamed")
        return self._session_view_record(record)

    def delete_session(self, session_id: str | None = None) -> SessionView:
        with self._lock:
            self._require_open()
            current = self._interactive.record
            target_id = current.session_id if session_id is None else session_id.strip()
            if self._running and target_id == current.session_id:
                raise SessionError(
                    "SESSION_BUSY",
                    "cannot delete a running session; cancel the task first",
                )
        try:
            selected = self.store.delete_session(target_id, current.workspace)
        except SessionError as exc:
            if (
                target_id != current.session_id
                or exc.error_code != "SESSION_NOT_FOUND"
                or current.messages
            ):
                raise
            selected = self.store.load_latest(current.workspace)
        self.manual_skills.remove_session(target_id)
        self._session_results.pop(target_id, None)
        if target_id == current.session_id:
            if selected is None:
                selected = self.store.create_session(
                    current.workspace, self.provider_name, self.config.model
                )
            self._activate_session(selected)
            result = self._session_view_record(selected)
        else:
            result = self._session_view_record(current)
        self._emit(ProductEventKind.SESSION_CHANGED, "Session deleted")
        return result

    def list_memory(self) -> tuple[MemoryView, ...]:
        return tuple(_memory_view(item) for item in self.memory_store.list(self.config.workspace))

    def add_memory(self, text: str) -> MemoryView:
        self._require_idle()
        item = self.memory_store.add(
            self.config.workspace, text, (self.config.api_key,)
        )
        self._refresh_memory()
        self._emit(ProductEventKind.STATE_CHANGED, "Memory added")
        return _memory_view(item)

    def delete_memory(self, item_id: str) -> bool:
        self._require_idle()
        deleted = self.memory_store.delete(self.config.workspace, item_id)
        if deleted:
            self._refresh_memory()
            self._emit(ProductEventKind.STATE_CHANGED, "Memory deleted")
        return deleted

    def clear_memory(self) -> None:
        self._require_idle()
        self.memory_store.clear(self.config.workspace)
        self._refresh_memory()
        self._emit(ProductEventKind.STATE_CHANGED, "Memory cleared")

    def list_skills(self) -> tuple[SkillView, ...]:
        manual = set(self.manual_skills.names(self._interactive.record.session_id))
        automatic = set(self._active_skill_names) - manual
        return tuple(
            SkillView(
                item.name,
                redact_product_text(item.description, (self.config.api_key,)),
                item.scope,
                "manual" if item.name in manual else "automatic" if item.name in automatic else "inactive",
            )
            for item in self.skill_registry.metadata
        )

    def use_skill(self, name: str) -> SkillView:
        self._require_idle()
        session_id = self._interactive.record.session_id
        self.manual_skills.use(session_id, name, self.skill_registry)
        self._active_skill_names = self.manual_skills.names(session_id)
        self._emit(ProductEventKind.STATE_CHANGED, f"Skill active: {name}")
        return next(item for item in self.list_skills() if item.name == name)

    def off_skill(self, name: str) -> None:
        self._require_idle()
        session_id = self._interactive.record.session_id
        self.manual_skills.off(session_id, name)
        self._active_skill_names = self.manual_skills.names(session_id)
        self._emit(ProductEventKind.STATE_CHANGED, f"Skill inactive: {name}")

    def clear_skills(self) -> None:
        self._require_idle()
        session_id = self._interactive.record.session_id
        self.manual_skills.clear(session_id)
        self._active_skill_names = ()
        self._emit(ProductEventKind.STATE_CHANGED, "Skills cleared")

    def list_plugins(self) -> tuple[PluginView, ...]:
        return tuple(self._plugin_view(item) for item in self._plugin_manager.discover())

    def enable_plugin(self, name: str) -> PluginView:
        self._require_idle()
        result = self._plugin_manager.enable(name)
        self._emit(ProductEventKind.STATE_CHANGED, f"Plugin enabled: {name}")
        return self._plugin_view(result)

    def disable_plugin(self, name: str) -> PluginView | None:
        self._require_idle()
        result = self._plugin_manager.disable(name)
        if result is not None:
            self._emit(ProductEventKind.STATE_CHANGED, f"Plugin disabled: {name}")
            return self._plugin_view(result)
        return None

    def recall(self, query: str) -> tuple[RecallView, ...]:
        self._require_idle()
        entries = self.recall_service.search(
            self.config.workspace,
            query,
            exclude_session_id=self._interactive.record.session_id,
        )
        self._pending_recall = entries
        self._emit(
            ProductEventKind.RECALL_RESULT,
            f"{len(entries)} recalled result(s)",
        )
        return tuple(_recall_view(item) for item in entries)

    def pending_candidates(self) -> tuple[MemoryCandidateView, ...]:
        return tuple(item.view for item in self._pending_candidates.values())

    def confirm_candidate(
        self, candidate_id: str, *, accept: bool
    ) -> MemoryView | None:
        self._require_idle()
        pending = self._pending_candidates.pop(candidate_id, None)
        if pending is None:
            raise SessionError("MEMORY_CANDIDATE_NOT_FOUND", "memory candidate was not found")
        if not accept:
            self._emit(ProductEventKind.STATE_CHANGED, "Memory candidate rejected")
            return None
        candidate = pending.candidate
        match = self.memory_store.match(
            self.config.workspace,
            candidate.content,
            candidate.kind,
            key=candidate.key,
        )
        if match.status == "conflict" and match.existing is not None:
            item = self.memory_store.replace(
                self.config.workspace,
                match.existing.id,
                candidate.content,
                (self.config.api_key,),
                kind=candidate.kind,
                source="confirmed_candidate",
                key=candidate.key,
            )
        else:
            item = self.memory_store.add(
                self.config.workspace,
                candidate.content,
                (self.config.api_key,),
                kind=candidate.kind,
                source="confirmed_candidate",
                key=candidate.key,
            )
        self._refresh_memory()
        self._emit(ProductEventKind.STATE_CHANGED, "Memory candidate saved")
        return _memory_view(item)

    def close(self) -> None:
        with self._lock:
            if self._closed or self._resources_closed:
                return
            if self._running:
                self._close_pending = True
                self._cancel_event.set()
                self._agent_state = AgentState.CANCELLING
                return
        self._close_resources()

    def _select_session(
        self, new_session: bool, resume_session: str | None
    ) -> SessionRecord:
        if resume_session is not None:
            return self.store.load_session(resume_session, self.config.workspace)
        if not new_session:
            latest = self.store.load_latest(self.config.workspace)
            if latest is not None:
                return latest
        return self.store.create_session(
            self.config.workspace, self.provider_name, self.config.model
        )

    def _prepare_turn(self, task: str) -> None:
        recall_entries = self._pending_recall
        self._pending_recall = ()
        if not recall_entries and should_automatic_recall(task):
            try:
                recall_entries = self.recall_service.search(
                    self.config.workspace,
                    task,
                    exclude_session_id=self._interactive.record.session_id,
                )
            except SessionError:
                recall_entries = ()
        self._runner.set_recalled_history(recall_entries)
        activation = self._skill_activator.prepare(
            task,
            self.manual_skills.names(self._interactive.record.session_id),
        )
        self._runner.set_active_skills(activation.skills)
        self._subagent_manager.set_active_skills(activation.skills)
        with self._lock:
            self._active_skill_names = tuple(
                item.skill.metadata.name for item in activation.skills
            )
        self._emit(ProductEventKind.STATE_CHANGED, "Task context prepared")

    def _capture_candidates(
        self, turn_messages: tuple[Message, ...], status: RunStatus
    ) -> None:
        if status is not RunStatus.FINAL_RESPONSE:
            return
        for candidate in self._candidate_extractor.extract(turn_messages):
            if not is_safe_candidate(candidate, (self.config.api_key,)):
                continue
            match = self.memory_store.match(
                self.config.workspace,
                candidate.content,
                candidate.kind,
                key=candidate.key,
            )
            if match.status in {"exact_duplicate", "normalized_duplicate"}:
                continue
            self._candidate_number += 1
            identifier = f"candidate-{self._candidate_number}"
            view = MemoryCandidateView(
                identifier,
                candidate.key,
                candidate.content,
                candidate.kind,
                candidate.source,
                "replace" if match.status == "conflict" else "save",
            )
            self._pending_candidates[identifier] = _PendingCandidate(view, candidate)
            self._emit(
                ProductEventKind.MEMORY_CANDIDATE,
                f"{view.key} = {view.content}",
            )

    def _activate_session(self, record: SessionRecord) -> None:
        self._interactive.activate(record)
        self._pending_recall = ()
        self._pending_candidates.clear()
        self._changes = ()
        self._verifications = ()
        self._active_skill_names = self.manual_skills.names(record.session_id)

    def _refresh_memory(self) -> None:
        memories = self.memory_store.context_items_for_context(self.config.workspace)
        self._runner.set_workspace_memories(memories)
        self._subagent_manager.set_workspace_memories(memories)

    def _session_view(self, summary: SessionSummary) -> SessionView:
        return SessionView(
            summary.session_id,
            summary.name,
            summary.updated_at,
            summary.session_id == self._interactive.record.session_id,
            self._running and summary.session_id == self._interactive.record.session_id,
            self._session_results.get(summary.session_id),
        )

    def _session_view_record(self, record: SessionRecord) -> SessionView:
        return SessionView(
            record.session_id,
            record.name,
            record.updated_at,
            record.session_id == self._interactive.record.session_id,
            self._running and record.session_id == self._interactive.record.session_id,
            self._session_results.get(record.session_id),
        )

    def _plugin_view(self, item: PluginInfo) -> PluginView:
        metadata = item.metadata
        return PluginView(
            metadata.name,
            metadata.version,
            redact_product_text(metadata.description, (self.config.api_key,)),
            item.status,
            item.status == "enabled",
            _PLUGIN_WARNING,
        )

    def _on_agent_event(self, event: AgentEvent) -> None:
        with self._lock:
            task_id = self._current_task_id
            session_id = self._interactive.record.session_id
        if task_id is None:
            return
        self._publish(
            adapt_agent_event(
                event,
                session_id=session_id,
                task_id=task_id,
                sensitive_values=(self.config.api_key,),
            )
        )

    def _on_subagent_event(self, event: SubagentEvent) -> None:
        with self._lock:
            task_id = self._current_task_id
            session_id = self._interactive.record.session_id
            if event.kind == "task_started":
                self._active_subagents += 1
            elif event.kind == "task_completed":
                self._active_subagents = max(0, self._active_subagents - 1)
        if task_id is None:
            return
        self._publish(
            adapt_subagent_event(
                event,
                session_id=session_id,
                task_id=task_id,
                sensitive_values=(self.config.api_key,),
            )
        )

    def _on_text_delta(self, text: str) -> None:
        self._emit(ProductEventKind.TEXT_DELTA, text, ActivityStatus.RUNNING)

    def _emit(
        self,
        kind: ProductEventKind,
        title: str,
        status: ActivityStatus | None = None,
        *,
        detail: str = "",
    ) -> None:
        with self._lock:
            session_id = self._interactive.record.session_id
            task_id = self._current_task_id
        self._publish(
            ProductEvent(
                kind,
                datetime.now(timezone.utc),
                session_id,
                task_id,
                None,
                redact_product_text(title, (self.config.api_key,)),
                redact_product_text(detail, (self.config.api_key,)),
                status,
                source=_product_event_source(kind),
            )
        )

    def _publish(self, event: ProductEvent) -> None:
        with self._lock:
            subscribers = tuple(self._subscribers)
        for subscriber in subscribers:
            try:
                subscriber(event)
            except Exception:
                pass

    def _require_idle(self) -> None:
        with self._lock:
            self._require_open()
            if self._running:
                raise SessionError("SESSION_BUSY", "session already has a running task")

    def _require_open(self) -> None:
        if self._closed or self._resources_closed:
            raise RuntimeError("application service is closed")

    def _close_resources(self) -> None:
        with self._lock:
            if self._resources_closed:
                return
            self._resources_closed = True
            self._closed = True
            self._agent_state = AgentState.CLOSED
        try:
            self._plugin_manager.close()
        finally:
            _close_client(self._client)


def _context_policy(config: RuntimeConfig) -> ContextPolicy:
    return ContextPolicy(
        max_context_chars=config.max_context_chars,
        max_tool_output_chars=config.max_tool_output_chars,
        recent_turns=config.recent_turns,
        minimum_recent_turns=min(2, config.recent_turns),
    )


def _memory_view(item: MemoryItem) -> MemoryView:
    return MemoryView(item.id, item.kind, item.key, item.content, item.source)


def _recall_view(item: RecallEntry) -> RecallView:
    return RecallView(
        item.session_id,
        item.source,
        item.excerpt,
        item.ordinal,
        item.timestamp,
        item.score,
    )


def _close_client(client: ModelClient) -> None:
    close = getattr(client, "close", None)
    if callable(close):
        close()


def _safe_exception_message(exc: BaseException) -> str:
    if isinstance(exc, SessionError):
        return f"{exc.error_code}: {exc.message}"
    text = str(exc).strip()
    return text or type(exc).__name__


def _failure_presentation(result: RunResult) -> str:
    detail = result.error or result.status.value
    category = {
        RunStatus.MODEL_ERROR: "Provider Error",
        RunStatus.INTERNAL_ERROR: "Internal Error",
        RunStatus.MAX_STEPS: "Agent Limit",
        RunStatus.STALLED: "Agent Stalled",
    }.get(result.status, "Task Error")
    return f"{category}: {detail}"


def _product_event_source(kind: ProductEventKind) -> ActivitySource:
    if kind in {ProductEventKind.ERROR, ProductEventKind.TASK_FAILED}:
        return ActivitySource.ERROR
    if kind is ProductEventKind.VERIFICATION:
        return ActivitySource.COMMAND_VERIFICATION
    return ActivitySource.TASK

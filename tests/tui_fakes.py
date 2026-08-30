from __future__ import annotations

import threading
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from coding_agent.application.events import (
    ActivityStatus,
    ProductEvent,
    ProductEventKind,
)
from coding_agent.application.state import (
    ActivityView,
    AgentState,
    ChangeStatus,
    ChangeView,
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
    VerificationView,
)
from coding_agent.protocol import RunResult, RunStatus


NOW = datetime(2026, 8, 29, tzinfo=timezone.utc)


class FakeProductService:
    def __init__(self, workspace: Path, *, blocking: bool = False) -> None:
        self.workspace = workspace
        self.subscribers = []
        self.tasks: list[str] = []
        self.cancel_count = 0
        self.close_count = 0
        self.new_count = 0
        self.switches: list[str] = []
        self.renames: list[str] = []
        self.delete_count = 0
        self.memory_actions: list[tuple[str, str]] = []
        self.skill_actions: list[tuple[str, str]] = []
        self.plugin_actions: list[tuple[str, str]] = []
        self.recall_queries: list[str] = []
        self.candidate_decisions: list[tuple[str, bool]] = []
        self.blocking = blocking
        self.started = threading.Event()
        self.release = threading.Event()
        self._session_id = "111111111111"
        self._sessions = (
            SessionView("111111111111", "Parser fix", NOW, True, False, None),
            SessionView("222222222222", "Tests", NOW, False, False, None),
        )
        self._conversation: tuple[ConversationItem, ...] = ()
        self._activities: tuple[ActivityView, ...] = ()
        self._changes: tuple[ChangeView, ...] = ()
        self._verifications: tuple[VerificationView, ...] = ()
        self._memories = (
            MemoryView("mem00001", "command", "test.command", "pytest -q", "user"),
        )
        self._skills = (
            SkillView("tdd", "Test first.", "user", "inactive"),
        )
        self._plugins = (
            PluginView("git-readonly", "1.0.0", "Read-only Git tools.", "disabled", False),
        )
        self._candidates: tuple[MemoryCandidateView, ...] = ()
        self._state = AgentState.READY

    def subscribe(self, sink):
        self.subscribers.append(sink)

        def unsubscribe() -> None:
            if sink in self.subscribers:
                self.subscribers.remove(sink)

        return unsubscribe

    def snapshot(self) -> ProductSnapshot:
        sessions = tuple(
            replace(
                item,
                active=item.session_id == self._session_id,
                running=(
                    self._state in {AgentState.RUNNING, AgentState.CANCELLING}
                    and item.session_id == self._session_id
                ),
            )
            for item in self._sessions
        )
        return ProductSnapshot(
            ProductStatus(
                "deepseek",
                "deepseek-v4-flash",
                self.workspace,
                self._session_id,
                self._state,
                400,
                10_000,
                False,
                2,
                ("tdd",),
                ("git-readonly",),
                0,
            ),
            sessions,
            self._conversation,
            self._activities,
            self._changes,
            self._verifications,
        )

    def get_status(self):
        return self.snapshot().status

    def submit_task(self, text: str) -> RunResult:
        self.tasks.append(text)
        self._state = AgentState.RUNNING
        self._emit(ProductEventKind.TASK_STARTED, text, ActivityStatus.RUNNING)
        self.started.set()
        if self.blocking:
            assert self.release.wait(timeout=5)
        if self.cancel_count:
            self._state = AgentState.READY
            self._emit(ProductEventKind.TASK_CANCELLED, "cancelled", ActivityStatus.CANCELLED)
            return RunResult(RunStatus.CANCELLED, None, 1, "cancelled")
        self._conversation += (
            ConversationItem(f"u-{len(self.tasks)}", ConversationKind.USER, text),
            ConversationItem(f"a-{len(self.tasks)}", ConversationKind.ASSISTANT, "Done **successfully**."),
        )
        self._emit(ProductEventKind.FINAL_RESPONSE, "Done **successfully**.", ActivityStatus.SUCCEEDED)
        self._state = AgentState.READY
        return RunResult(RunStatus.FINAL_RESPONSE, "Done **successfully**.", 1, None)

    def cancel_task(self) -> bool:
        if self._state is not AgentState.RUNNING:
            return False
        self.cancel_count += 1
        self._state = AgentState.CANCELLING
        self.release.set()
        return True

    def close(self) -> None:
        self.close_count += 1
        self._state = AgentState.CLOSED

    def new_session(self) -> SessionView:
        self.new_count += 1
        identifier = "333333333333"
        created = SessionView(identifier, None, NOW, True, False, None)
        self._sessions += (created,)
        self._session_id = identifier
        self._conversation = ()
        return created

    def switch_session(self, session_id: str) -> SessionView:
        self.switches.append(session_id)
        self._session_id = session_id
        return next(item for item in self._sessions if item.session_id == session_id)

    def rename_session(self, name: str) -> SessionView:
        self.renames.append(name)
        self._sessions = tuple(
            replace(item, name=name) if item.session_id == self._session_id else item
            for item in self._sessions
        )
        return next(item for item in self._sessions if item.session_id == self._session_id)

    def delete_session(self) -> SessionView:
        self.delete_count += 1
        self._sessions = tuple(item for item in self._sessions if item.session_id != self._session_id)
        self._session_id = self._sessions[0].session_id
        return self._sessions[0]

    def search_sessions(self, query: str) -> tuple[SessionView, ...]:
        needle = query.casefold()
        return tuple(item for item in self._sessions if needle in item.display_name.casefold())

    def list_sessions(self) -> tuple[SessionView, ...]:
        return self.snapshot().sessions

    def list_memory(self) -> tuple[MemoryView, ...]:
        return self._memories

    def add_memory(self, text: str) -> MemoryView:
        self.memory_actions.append(("add", text))
        item = MemoryView("mem00002", "fact", "fact", text, "user")
        self._memories += (item,)
        return item

    def delete_memory(self, item_id: str) -> bool:
        self.memory_actions.append(("delete", item_id))
        self._memories = tuple(item for item in self._memories if item.id != item_id)
        return True

    def clear_memory(self) -> None:
        self.memory_actions.append(("clear", ""))
        self._memories = ()

    def list_skills(self) -> tuple[SkillView, ...]:
        return self._skills

    def use_skill(self, name: str) -> SkillView:
        self.skill_actions.append(("use", name))
        self._skills = tuple(replace(item, activation="manual") if item.name == name else item for item in self._skills)
        return self._skills[0]

    def off_skill(self, name: str) -> None:
        self.skill_actions.append(("off", name))
        self._skills = tuple(replace(item, activation="inactive") if item.name == name else item for item in self._skills)

    def clear_skills(self) -> None:
        self.skill_actions.append(("clear", ""))

    def list_plugins(self) -> tuple[PluginView, ...]:
        return self._plugins

    def enable_plugin(self, name: str) -> PluginView:
        self.plugin_actions.append(("enable", name))
        self._plugins = tuple(replace(item, status="enabled", enabled=True) if item.name == name else item for item in self._plugins)
        return self._plugins[0]

    def disable_plugin(self, name: str) -> PluginView:
        self.plugin_actions.append(("disable", name))
        self._plugins = tuple(replace(item, status="disabled", enabled=False) if item.name == name else item for item in self._plugins)
        return self._plugins[0]

    def recall(self, query: str) -> tuple[RecallView, ...]:
        self.recall_queries.append(query)
        return (RecallView("222222222222", "assistant", "parser failed earlier", 3, NOW, 10),)

    def pending_candidates(self) -> tuple[MemoryCandidateView, ...]:
        return self._candidates

    def confirm_candidate(self, candidate_id: str, *, accept: bool):
        self.candidate_decisions.append((candidate_id, accept))
        self._candidates = ()
        return None

    def _emit(
        self,
        kind: ProductEventKind,
        title: str,
        status: ActivityStatus | None,
    ) -> None:
        event = ProductEvent(
            kind, NOW, self._session_id, "task-1", None, title, status=status
        )
        for subscriber in tuple(self.subscribers):
            subscriber(event)

    def publish(self, event: ProductEvent) -> None:
        for subscriber in tuple(self.subscribers):
            subscriber(event)

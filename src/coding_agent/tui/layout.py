"""Deterministic side-pane sizing for the TUI."""

from __future__ import annotations

from dataclasses import dataclass


DEFAULT_SESSIONS_WIDTH = 30
MIN_SESSIONS_WIDTH = 24
MAX_SESSIONS_WIDTH = 48

DEFAULT_ACTIVITY_WIDTH = 42
MIN_ACTIVITY_WIDTH = 28
MAX_ACTIVITY_WIDTH = 60

MIN_CENTER_WIDTH = 40
COMPACT_WIDTH = 96
PANE_RESIZE_STEP = 4


@dataclass(frozen=True, slots=True)
class PanePreferences:
    """Process-local user preferences, independent from responsive visibility."""

    sessions_width: int = DEFAULT_SESSIONS_WIDTH
    activity_width: int = DEFAULT_ACTIVITY_WIDTH
    show_sessions: bool = True
    show_activity: bool = True


@dataclass(frozen=True, slots=True)
class PaneLayout:
    """Actual pane visibility and widths for one terminal size."""

    show_sessions: bool
    sessions_width: int
    show_activity: bool
    activity_width: int
    center_width: int


def calculate_pane_layout(width: int, preferences: PanePreferences) -> PaneLayout:
    """Fit requested panes while reserving a usable center conversation column."""

    available_width = max(0, int(width))
    sessions_width = _clamp(
        preferences.sessions_width,
        MIN_SESSIONS_WIDTH,
        MAX_SESSIONS_WIDTH,
    )
    activity_width = _clamp(
        preferences.activity_width,
        MIN_ACTIVITY_WIDTH,
        MAX_ACTIVITY_WIDTH,
    )

    show_sessions = preferences.show_sessions and available_width >= COMPACT_WIDTH
    show_activity = (
        preferences.show_activity
        and available_width >= MIN_CENTER_WIDTH + MIN_ACTIVITY_WIDTH
    )

    if show_sessions and show_activity:
        side_budget = max(0, available_width - MIN_CENTER_WIDTH)
        overflow = max(0, sessions_width + activity_width - side_budget)
        activity_reduction = min(overflow, activity_width - MIN_ACTIVITY_WIDTH)
        activity_width -= activity_reduction
        overflow -= activity_reduction
        sessions_width -= min(overflow, sessions_width - MIN_SESSIONS_WIDTH)
    elif show_sessions:
        sessions_width = min(
            sessions_width,
            max(MIN_SESSIONS_WIDTH, available_width - MIN_CENTER_WIDTH),
        )
    elif show_activity:
        activity_width = min(
            activity_width,
            max(MIN_ACTIVITY_WIDTH, available_width - MIN_CENTER_WIDTH),
        )

    actual_sessions_width = sessions_width if show_sessions else 0
    actual_activity_width = activity_width if show_activity else 0
    center_width = max(
        0,
        available_width - actual_sessions_width - actual_activity_width,
    )
    return PaneLayout(
        show_sessions=show_sessions,
        sessions_width=actual_sessions_width,
        show_activity=show_activity,
        activity_width=actual_activity_width,
        center_width=center_width,
    )


def _clamp(value: int, minimum: int, maximum: int) -> int:
    return max(minimum, min(maximum, int(value)))

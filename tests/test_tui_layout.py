from coding_agent.tui.layout import (
    DEFAULT_ACTIVITY_WIDTH,
    DEFAULT_SESSIONS_WIDTH,
    MAX_ACTIVITY_WIDTH,
    MAX_SESSIONS_WIDTH,
    MIN_ACTIVITY_WIDTH,
    MIN_CENTER_WIDTH,
    MIN_SESSIONS_WIDTH,
    PanePreferences,
    calculate_pane_layout,
)


def test_wide_layout_uses_requested_preferred_widths() -> None:
    layout = calculate_pane_layout(140, PanePreferences())

    assert layout.show_sessions is True
    assert layout.sessions_width == DEFAULT_SESSIONS_WIDTH
    assert layout.show_activity is True
    assert layout.activity_width == DEFAULT_ACTIVITY_WIDTH
    assert layout.center_width >= MIN_CENTER_WIDTH


def test_layout_clamps_preferences_to_documented_limits() -> None:
    narrow = calculate_pane_layout(
        180,
        PanePreferences(sessions_width=1, activity_width=1),
    )
    wide = calculate_pane_layout(
        220,
        PanePreferences(sessions_width=999, activity_width=999),
    )

    assert narrow.sessions_width == MIN_SESSIONS_WIDTH
    assert narrow.activity_width == MIN_ACTIVITY_WIDTH
    assert wide.sessions_width == MAX_SESSIONS_WIDTH
    assert wide.activity_width == MAX_ACTIVITY_WIDTH


def test_compact_layout_hides_sessions_and_protects_center() -> None:
    layout = calculate_pane_layout(80, PanePreferences())

    assert layout.show_sessions is False
    assert layout.sessions_width == 0
    assert layout.show_activity is True
    assert layout.center_width >= MIN_CENTER_WIDTH


def test_ultra_narrow_layout_hides_both_side_panes() -> None:
    layout = calculate_pane_layout(64, PanePreferences())

    assert layout.show_sessions is False
    assert layout.show_activity is False
    assert layout.center_width == 64


def test_user_visibility_requests_are_respected() -> None:
    layout = calculate_pane_layout(
        140,
        PanePreferences(show_sessions=False, show_activity=False),
    )

    assert layout.show_sessions is False
    assert layout.show_activity is False
    assert layout.center_width == 140


def test_medium_layout_shrinks_panes_before_sacrificing_center() -> None:
    layout = calculate_pane_layout(
        96,
        PanePreferences(sessions_width=MAX_SESSIONS_WIDTH, activity_width=MAX_ACTIVITY_WIDTH),
    )

    assert layout.show_sessions is True
    assert layout.show_activity is True
    assert layout.sessions_width >= MIN_SESSIONS_WIDTH
    assert layout.activity_width >= MIN_ACTIVITY_WIDTH
    assert layout.center_width >= MIN_CENTER_WIDTH


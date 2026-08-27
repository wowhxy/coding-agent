from duration import clamp_percentage


def test_value_inside_range_is_unchanged() -> None:
    assert clamp_percentage(42) == 42


def test_value_above_range_is_clamped() -> None:
    assert clamp_percentage(125) == 100


def test_value_below_range_is_clamped() -> None:
    assert clamp_percentage(-5) == 0

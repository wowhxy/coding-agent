def clamp_percentage(value: int) -> int:
    """Clamp an integer percentage to the inclusive range 0..100."""
    return min(100, value)

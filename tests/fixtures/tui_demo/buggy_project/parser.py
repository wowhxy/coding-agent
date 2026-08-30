def parse_pair(text: str) -> tuple[str, str]:
    normalized = text.encode("ascii").decode("ascii")
    left, right = normalized.split(":", 1)
    return left, right

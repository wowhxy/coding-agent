from __future__ import annotations

import os
from pathlib import Path

import pytest

from coding_agent.skills import (
    MAX_SKILL_BODY_CHARS,
    SkillError,
    SkillRegistry,
)


def _write_skill(
    root: Path,
    directory: str,
    *,
    name: str,
    description: str = "Useful project guidance.",
    body: str = "Inspect the project before editing.",
) -> Path:
    package = root / directory
    package.mkdir(parents=True)
    path = package / "SKILL.md"
    path.write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n{body}\n",
        encoding="utf-8",
    )
    return path


def _registry(tmp_path: Path) -> tuple[SkillRegistry, Path, Path]:
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    home.mkdir()
    workspace.mkdir()
    return SkillRegistry(home, workspace), home, workspace


def test_discovery_reads_metadata_without_decoding_body(tmp_path: Path) -> None:
    registry, home, _workspace = _registry(tmp_path)
    path = home / "skills" / "python-method" / "SKILL.md"
    path.parent.mkdir(parents=True)
    path.write_bytes(
        b"---\nname: python-method\ndescription: Python workflow.\n---\n\n\xff"
    )

    metadata = registry.discover()

    assert [(item.name, item.description, item.scope) for item in metadata] == [
        ("python-method", "Python workflow.", "user")
    ]
    with pytest.raises(SkillError) as raised:
        registry.load("python-method")
    assert raised.value.error_code == "SKILL_INVALID_UTF8"


def test_load_returns_unicode_body_and_revalidates_metadata(tmp_path: Path) -> None:
    registry, home, _workspace = _registry(tmp_path)
    path = _write_skill(
        home / "skills",
        "cmake",
        name="cpp-cmake",
        description="CMake 构建方法。",
        body="先读取 CMakeLists.txt，再运行测试。",
    )
    metadata = registry.discover()[0]

    skill = registry.load("cpp-cmake")

    assert skill.metadata == metadata
    assert skill.body == "先读取 CMakeLists.txt，再运行测试。"

    path.write_text(
        "---\nname: renamed\ndescription: changed\n---\nbody\n", encoding="utf-8"
    )
    with pytest.raises(SkillError) as raised:
        registry.load("cpp-cmake")
    assert raised.value.error_code == "SKILL_CHANGED"


@pytest.mark.parametrize(
    ("contents", "code"),
    (
        ("name: no-fence\ndescription: bad\nbody", "SKILL_INVALID_METADATA"),
        ("---\nname: Missing_Upper\ndescription: bad\n---\nbody", "SKILL_INVALID_NAME"),
        ("---\nname: bad--name\ndescription: bad\n---\nbody", "SKILL_INVALID_NAME"),
        ("---\nname: okay\nunknown: bad\ndescription: x\n---\nbody", "SKILL_INVALID_METADATA"),
        ("---\nname: okay\nname: duplicate\ndescription: x\n---\nbody", "SKILL_INVALID_METADATA"),
    ),
)
def test_malformed_packages_are_skipped_with_stable_diagnostics(
    tmp_path: Path, contents: str, code: str
) -> None:
    registry, home, _workspace = _registry(tmp_path)
    package = home / "skills" / "bad"
    package.mkdir(parents=True)
    (package / "SKILL.md").write_text(contents, encoding="utf-8")

    assert registry.discover() == ()
    assert [diagnostic.code for diagnostic in registry.diagnostics] == [code]
    assert all(contents not in item.message for item in registry.diagnostics)


def test_empty_body_is_discoverable_but_cannot_activate(tmp_path: Path) -> None:
    registry, home, _workspace = _registry(tmp_path)
    _write_skill(home / "skills", "empty", name="empty-skill", body="   ")

    assert [item.name for item in registry.discover()] == ["empty-skill"]
    with pytest.raises(SkillError) as raised:
        registry.load("empty-skill")
    assert raised.value.error_code == "SKILL_EMPTY_BODY"


def test_oversized_body_is_discoverable_but_cannot_activate(tmp_path: Path) -> None:
    registry, home, _workspace = _registry(tmp_path)
    _write_skill(
        home / "skills",
        "large",
        name="large-skill",
        body="x" * (MAX_SKILL_BODY_CHARS + 1),
    )

    assert [item.name for item in registry.discover()] == ["large-skill"]
    with pytest.raises(SkillError) as raised:
        registry.load("large-skill")
    assert raised.value.error_code == "SKILL_BODY_TOO_LARGE"


def test_exact_unicode_body_character_limit_can_activate(tmp_path: Path) -> None:
    registry, home, _workspace = _registry(tmp_path)
    body = "𐀀" * MAX_SKILL_BODY_CHARS
    _write_skill(
        home / "skills",
        "unicode-limit",
        name="unicode-limit",
        body=body,
    )
    registry.discover()

    assert registry.load("unicode-limit").body == body


def test_workspace_precedence_and_same_scope_duplicates_are_deterministic(
    tmp_path: Path,
) -> None:
    registry, home, workspace = _registry(tmp_path)
    _write_skill(home / "skills", "z-user", name="shared", body="user body")
    _write_skill(workspace / ".coding-agent" / "skills", "z", name="shared", body="z")
    _write_skill(workspace / ".coding-agent" / "skills", "a", name="shared", body="a")
    _write_skill(home / "skills", "python", name="python-method")

    metadata = registry.discover()

    assert [(item.name, item.scope) for item in metadata] == [
        ("python-method", "user"),
        ("shared", "user"),
    ]
    duplicate_paths = [
        item.path.parent.name
        for item in registry.diagnostics
        if item.code == "SKILL_DUPLICATE_NAME"
    ]
    assert duplicate_paths == ["a", "z"]
    assert registry.load("shared").body == "user body"


def test_valid_workspace_skill_overrides_user_skill(tmp_path: Path) -> None:
    registry, home, workspace = _registry(tmp_path)
    _write_skill(home / "skills", "shared", name="shared", body="user")
    workspace_path = _write_skill(
        workspace / ".coding-agent" / "skills",
        "shared",
        name="shared",
        body="workspace",
    )

    metadata = registry.discover()

    assert metadata[0].scope == "workspace"
    assert metadata[0].path == workspace_path
    assert registry.load("shared").body == "workspace"


def test_missing_roots_are_empty_without_diagnostics(tmp_path: Path) -> None:
    registry, _home, _workspace = _registry(tmp_path)

    assert registry.discover() == ()
    assert registry.diagnostics == ()


def test_nonexistent_home_is_valid_when_no_user_skills_exist(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    home = tmp_path / "not-created-yet"

    registry = SkillRegistry(home, workspace)

    assert registry.discover() == ()
    assert registry.diagnostics == ()
    assert not home.exists()


@pytest.mark.parametrize("target_kind", ("package", "file"))
def test_discovery_skips_symlinked_package_or_file(
    tmp_path: Path, target_kind: str
) -> None:
    registry, home, _workspace = _registry(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    outside_skill = _write_skill(outside, "real", name="escaped")
    root = home / "skills"
    root.mkdir()
    try:
        if target_kind == "package":
            os.symlink(outside_skill.parent, root / "linked", target_is_directory=True)
        else:
            package = root / "linked"
            package.mkdir()
            os.symlink(outside_skill, package / "SKILL.md")
    except OSError as exc:
        pytest.skip(f"symlinks unavailable: {exc}")

    assert registry.discover() == ()
    assert [item.code for item in registry.diagnostics] == ["SKILL_UNSAFE_PATH"]


def test_discovery_skips_symlinked_scope_root(tmp_path: Path) -> None:
    registry, home, _workspace = _registry(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    _write_skill(outside, "real", name="escaped")
    try:
        os.symlink(outside, home / "skills", target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlinks unavailable: {exc}")

    assert registry.discover() == ()
    assert [item.code for item in registry.diagnostics] == ["SKILL_UNSAFE_PATH"]


def test_unknown_skill_has_stable_error_without_echoing_input(tmp_path: Path) -> None:
    registry, _home, _workspace = _registry(tmp_path)
    registry.discover()

    with pytest.raises(SkillError) as raised:
        registry.load("secret-looking-name")

    assert raised.value.error_code == "SKILL_NOT_FOUND"
    assert "secret-looking-name" not in raised.value.message

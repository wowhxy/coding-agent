from __future__ import annotations

import json
from pathlib import Path

import pytest

from coding_agent.plugins import PluginManager
from coding_agent.tools.registry import ToolRegistry


def _package(
    home: Path,
    directory: str,
    *,
    manifest: object | None = None,
    entrypoint: str = "plugin.py",
    source: str = "",
) -> Path:
    package = home / "plugins" / directory
    package.mkdir(parents=True)
    document = (
        {
            "name": directory,
            "version": "1.0.0",
            "description": f"{directory} tools",
            "entrypoint": entrypoint,
        }
        if manifest is None
        else manifest
    )
    (package / "plugin.json").write_text(
        json.dumps(document), encoding="utf-8"
    )
    target = package / entrypoint
    if entrypoint == "plugin.py":
        target.write_text(source, encoding="utf-8")
    return package


def _manager(home: Path, workspace: Path) -> PluginManager:
    return PluginManager(home, workspace, ToolRegistry())


def test_discovery_parses_valid_manifest_without_importing_entrypoint(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    sentinel = tmp_path / "imported.txt"
    source = f"from pathlib import Path\nPath({str(sentinel)!r}).write_text('bad')\n"
    package = _package(home, "git-readonly", source=source)

    manager = _manager(home, workspace)
    discovered = manager.discover()

    assert len(discovered) == 1
    assert discovered[0].status == "disabled"
    assert discovered[0].metadata.name == "git-readonly"
    assert discovered[0].metadata.version == "1.0.0"
    assert discovered[0].metadata.description == "git-readonly tools"
    assert discovered[0].metadata.entrypoint == "plugin.py"
    assert discovered[0].metadata.package_dir == package.resolve()
    assert manager.diagnostics == ()
    assert not sentinel.exists()


@pytest.mark.parametrize(
    "document",
    [
        None,
        {},
        {
            "name": "bad name",
            "version": "1.0.0",
            "description": "tools",
            "entrypoint": "plugin.py",
        },
        {
            "name": "okay",
            "version": "",
            "description": "tools",
            "entrypoint": "plugin.py",
        },
        {
            "name": "okay",
            "version": "1.0.0",
            "description": 7,
            "entrypoint": "plugin.py",
        },
        {
            "name": "okay",
            "version": "1.0.0",
            "description": "tools",
            "entrypoint": "plugin.py",
            "extra": True,
        },
    ],
)
def test_malformed_manifests_are_diagnostic_not_discovered(
    tmp_path: Path, document: object | None
) -> None:
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    package = home / "plugins" / "package"
    package.mkdir(parents=True)
    manifest = package / "plugin.json"
    if document is None:
        manifest.write_text("{", encoding="utf-8")
    else:
        manifest.write_text(json.dumps(document), encoding="utf-8")
    (package / "plugin.py").write_text("", encoding="utf-8")

    manager = _manager(home, workspace)

    assert manager.discover() == ()
    assert [item.code for item in manager.diagnostics] == [
        "PLUGIN_MANIFEST_INVALID"
    ]
    assert "{" not in manager.diagnostics[0].message


def test_duplicate_manifest_names_reject_all_colliding_packages(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    manifest = {
        "name": "same-name",
        "version": "1.0.0",
        "description": "duplicate",
        "entrypoint": "plugin.py",
    }
    _package(home, "first-package", manifest=manifest)
    _package(home, "second-package", manifest=manifest)

    manager = _manager(home, workspace)

    assert manager.discover() == ()
    assert [item.code for item in manager.diagnostics] == [
        "PLUGIN_DUPLICATE_NAME",
        "PLUGIN_DUPLICATE_NAME",
    ]
    assert all(item.plugin_name == "same-name" for item in manager.diagnostics)


@pytest.mark.parametrize("entrypoint_kind", ["absolute", "escape", "missing"])
def test_unsafe_or_missing_entrypoint_is_rejected(
    tmp_path: Path, entrypoint_kind: str
) -> None:
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text("", encoding="utf-8")
    entrypoint = {
        "absolute": str(outside.resolve()),
        "escape": "../outside.py",
        "missing": "missing.py",
    }[entrypoint_kind]
    _package(home, "package", entrypoint=entrypoint)

    manager = _manager(home, workspace)

    assert manager.discover() == ()
    expected = (
        "PLUGIN_ENTRYPOINT_MISSING"
        if entrypoint_kind == "missing"
        else "PLUGIN_PATH_UNSAFE"
    )
    assert [item.code for item in manager.diagnostics] == [expected]


def test_discovery_never_reads_workspace_plugin_directory(tmp_path: Path) -> None:
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _package(workspace / ".coding-agent", "workspace-plugin")

    manager = _manager(home, workspace)

    assert manager.discover() == ()
    assert manager.diagnostics == ()


def test_package_manifest_and_entrypoint_symlinks_are_rejected(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    plugins = home / "plugins"
    plugins.mkdir(parents=True)
    outside_package = tmp_path / "outside-package"
    outside_package.mkdir()
    (outside_package / "plugin.json").write_text(
        json.dumps(
            {
                "name": "linked-package",
                "version": "1.0.0",
                "description": "linked",
                "entrypoint": "plugin.py",
            }
        ),
        encoding="utf-8",
    )
    (outside_package / "plugin.py").write_text("", encoding="utf-8")
    try:
        (plugins / "linked-package").symlink_to(
            outside_package, target_is_directory=True
        )
        manifest_package = _package(home, "linked-manifest")
        (manifest_package / "plugin.json").unlink()
        (manifest_package / "plugin.json").symlink_to(
            outside_package / "plugin.json"
        )
        entrypoint_package = _package(home, "linked-entrypoint")
        (entrypoint_package / "plugin.py").unlink()
        (entrypoint_package / "plugin.py").symlink_to(
            outside_package / "plugin.py"
        )
    except OSError as exc:
        pytest.skip(f"symlinks unavailable: {exc}")

    manager = _manager(home, workspace)

    assert manager.discover() == ()
    assert [item.code for item in manager.diagnostics] == [
        "PLUGIN_PATH_UNSAFE",
        "PLUGIN_PATH_UNSAFE",
        "PLUGIN_PATH_UNSAFE",
    ]

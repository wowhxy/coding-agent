"""Trusted local Plugin discovery and runtime integration."""

from __future__ import annotations

import json
import hashlib
import importlib.util
import re
import secrets
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .session_store import _atomic_write_text
from .tools.registry import ToolRegistry


_PLUGIN_NAME = re.compile(r"[a-z][a-z0-9-]{0,63}")
_MANIFEST_FIELDS = frozenset(
    {"name", "version", "description", "entrypoint"}
)


@dataclass(frozen=True, slots=True)
class PluginMetadata:
    name: str
    version: str
    description: str
    entrypoint: str
    package_dir: Path


@dataclass(frozen=True, slots=True)
class PluginInfo:
    metadata: PluginMetadata
    status: str


@dataclass(frozen=True, slots=True)
class PluginDiagnostic:
    code: str
    plugin_name: str | None
    message: str


@dataclass(frozen=True, slots=True)
class PluginContext:
    workspace: Path


class PluginError(RuntimeError):
    """A sanitized, stable Plugin lifecycle failure."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class PluginManager:
    """Discover trusted-home manifests without importing executable code."""

    def __init__(
        self, home: Path, workspace: Path, registry: ToolRegistry
    ) -> None:
        self.home = Path(home).resolve(strict=False)
        resolved_workspace = Path(workspace).resolve(strict=True)
        if not resolved_workspace.is_dir():
            raise ValueError("plugin workspace must be a directory")
        self.workspace = resolved_workspace
        self.registry = registry
        self.plugin_root = self.home / "plugins"
        self._metadata: dict[str, PluginMetadata] = {}
        self._diagnostics: tuple[PluginDiagnostic, ...] = ()
        self._loaded_modules: dict[str, str] = {}
        self._enabled_names: set[str] = set()
        self._discovered = False
        self.state_path = self.home / "plugins.json"

    @property
    def diagnostics(self) -> tuple[PluginDiagnostic, ...]:
        return self._diagnostics

    @property
    def enabled_names(self) -> tuple[str, ...]:
        return tuple(sorted(self._enabled_names))

    def discover(self) -> tuple[PluginInfo, ...]:
        """Index valid manifests in stable order without importing entrypoints."""

        self._metadata = {}
        self._discovered = True
        diagnostics: list[PluginDiagnostic] = []
        candidates: list[PluginMetadata] = []
        if not self.plugin_root.exists():
            self._diagnostics = ()
            return ()
        if self.plugin_root.is_symlink() or not self.plugin_root.is_dir():
            self._diagnostics = (
                PluginDiagnostic(
                    "PLUGIN_PATH_UNSAFE",
                    None,
                    "plugin root is unsafe",
                ),
            )
            return ()
        try:
            canonical_root = self.plugin_root.resolve(strict=True)
            entries = sorted(
                self.plugin_root.iterdir(), key=lambda item: item.name
            )
        except OSError:
            self._diagnostics = (
                PluginDiagnostic(
                    "PLUGIN_DISCOVERY_FAILED",
                    None,
                    "plugins could not be inspected",
                ),
            )
            return ()

        for package in entries:
            if package.is_symlink():
                diagnostics.append(
                    PluginDiagnostic(
                        "PLUGIN_PATH_UNSAFE",
                        package.name,
                        "plugin package path is unsafe",
                    )
                )
                continue
            if not package.is_dir():
                continue
            try:
                canonical_package = package.resolve(strict=True)
            except OSError:
                canonical_package = None
            if (
                canonical_package is None
                or not canonical_package.is_relative_to(canonical_root)
            ):
                diagnostics.append(
                    PluginDiagnostic(
                        "PLUGIN_PATH_UNSAFE",
                        package.name,
                        "plugin package path is unsafe",
                    )
                )
                continue
            metadata, diagnostic = _read_manifest(package)
            if diagnostic is not None:
                diagnostics.append(diagnostic)
            elif metadata is not None:
                candidates.append(metadata)

        by_name: dict[str, list[PluginMetadata]] = {}
        for metadata in candidates:
            by_name.setdefault(metadata.name, []).append(metadata)
        for metadata in candidates:
            if len(by_name[metadata.name]) > 1:
                diagnostics.append(
                    PluginDiagnostic(
                        "PLUGIN_DUPLICATE_NAME",
                        metadata.name,
                        "multiple plugin packages declare the same name",
                    )
                )
            else:
                self._metadata[metadata.name] = metadata

        self._diagnostics = tuple(
            sorted(
                diagnostics,
                key=lambda item: (
                    item.plugin_name or "",
                    item.code,
                    item.message,
                ),
            )
        )
        return tuple(
            PluginInfo(
                metadata,
                "enabled"
                if metadata.name in self._loaded_modules
                else "disabled",
            )
            for metadata in sorted(
                self._metadata.values(), key=lambda item: item.name
            )
        )

    def enable(self, name: str, *, persist: bool = True) -> PluginInfo:
        """Import and transactionally register one explicitly trusted plugin."""

        if name in self._loaded_modules:
            metadata = self._metadata.get(name)
            if metadata is None:
                raise self._error(
                    "PLUGIN_ENABLE_FAILED", name, "enabled plugin metadata is unavailable"
                )
            if persist and name not in self._enabled_names:
                previous = set(self._enabled_names)
                self._enabled_names.add(name)
                try:
                    self._write_enabled_state()
                except OSError:
                    self._enabled_names = previous
                    raise self._error(
                        "PLUGIN_STATE_SAVE_FAILED",
                        name,
                        "plugin enabled state could not be saved",
                    ) from None
            return PluginInfo(metadata, "enabled")
        if not self._discovered or name not in self._metadata:
            self.discover()
        metadata = self._metadata.get(name)
        if metadata is None:
            raise self._error(
                "PLUGIN_NOT_FOUND", name, "plugin is not installed or valid"
            )

        entrypoint = metadata.package_dir / metadata.entrypoint
        module_name = _module_name(metadata.name, entrypoint)
        try:
            specification = importlib.util.spec_from_file_location(
                module_name, entrypoint
            )
            if specification is None or specification.loader is None:
                raise ImportError
            module = importlib.util.module_from_spec(specification)
            sys.modules[module_name] = module
            specification.loader.exec_module(module)
        except (Exception, SystemExit):
            sys.modules.pop(module_name, None)
            raise self._error(
                "PLUGIN_IMPORT_FAILED", name, "plugin entrypoint could not be imported"
            ) from None

        get_tools = getattr(module, "get_tools", None)
        if not callable(get_tools):
            sys.modules.pop(module_name, None)
            raise self._error(
                "PLUGIN_CONTRACT_INVALID",
                name,
                "plugin does not provide a valid get_tools function",
            )
        try:
            provided = get_tools(PluginContext(self.workspace))
        except (Exception, SystemExit):
            sys.modules.pop(module_name, None)
            raise self._error(
                "PLUGIN_ENABLE_FAILED", name, "plugin tool creation failed"
            ) from None
        if type(provided) not in {tuple, list} or not provided:
            sys.modules.pop(module_name, None)
            raise self._error(
                "PLUGIN_CONTRACT_INVALID",
                name,
                "plugin get_tools must return a non-empty tuple or list",
            )
        tools = tuple(provided)
        try:
            self.registry.register_many(tools, source=f"plugin:{name}")
        except ValueError as error:
            sys.modules.pop(module_name, None)
            code = (
                "PLUGIN_TOOL_COLLISION"
                if str(error).startswith("duplicate tool:")
                else "PLUGIN_TOOL_INVALID"
            )
            raise self._error(
                code,
                name,
                "plugin tool names collide"
                if code == "PLUGIN_TOOL_COLLISION"
                else "plugin contains an invalid tool",
            ) from None
        except TypeError:
            sys.modules.pop(module_name, None)
            raise self._error(
                "PLUGIN_TOOL_INVALID", name, "plugin contains an invalid tool"
            ) from None

        self._loaded_modules[name] = module_name
        if persist:
            previous = set(self._enabled_names)
            self._enabled_names.add(name)
            try:
                self._write_enabled_state()
            except OSError:
                self._enabled_names = previous
                self.registry.unregister_source(f"plugin:{name}")
                self._loaded_modules.pop(name, None)
                sys.modules.pop(module_name, None)
                raise self._error(
                    "PLUGIN_STATE_SAVE_FAILED",
                    name,
                    "plugin enabled state could not be saved",
                ) from None
        return PluginInfo(metadata, "enabled")

    def disable(
        self, name: str, *, persist: bool = True
    ) -> PluginInfo | None:
        """Remove only one plugin's registered tools and imported module."""

        known = (
            name in self._loaded_modules
            or name in self._enabled_names
            or name in self._metadata
        )
        if not known:
            return None
        if persist and name in self._enabled_names:
            previous = set(self._enabled_names)
            self._enabled_names.discard(name)
            try:
                self._write_enabled_state()
            except OSError:
                self._enabled_names = previous
                raise self._error(
                    "PLUGIN_STATE_SAVE_FAILED",
                    name,
                    "plugin enabled state could not be saved",
                ) from None
        module_name = self._loaded_modules.pop(name, None)
        if module_name is not None:
            self.registry.unregister_source(f"plugin:{name}")
            sys.modules.pop(module_name, None)
        metadata = self._metadata.get(name)
        return PluginInfo(metadata, "disabled") if metadata is not None else None

    def _error(
        self, code: str, plugin_name: str | None, message: str
    ) -> PluginError:
        diagnostic = PluginDiagnostic(code, plugin_name, message)
        self._diagnostics = self._diagnostics + (diagnostic,)
        return PluginError(code, message)

    def restore_enabled(self) -> tuple[PluginInfo, ...]:
        """Best-effort restore of previously explicitly enabled plugins."""

        self.discover()
        names = self._read_enabled_state()
        self._enabled_names = set(names)
        restored: list[PluginInfo] = []
        for name in names:
            if name not in self._metadata:
                self._diagnose(
                    "PLUGIN_ENABLED_MISSING",
                    name,
                    "previously enabled plugin is missing or invalid",
                )
                continue
            try:
                restored.append(self.enable(name, persist=False))
            except PluginError:
                continue
        return tuple(restored)

    def load_snapshot(
        self, names: tuple[str, ...]
    ) -> tuple[PluginInfo, ...]:
        """Load an immutable runtime snapshot without reading or writing state."""

        if type(names) is not tuple or any(
            type(name) is not str or _PLUGIN_NAME.fullmatch(name) is None
            for name in names
        ):
            self._diagnose(
                "PLUGIN_SNAPSHOT_INVALID", None, "plugin snapshot is invalid"
            )
            return ()
        requested = tuple(sorted(set(names)))
        for loaded_name in tuple(self._loaded_modules):
            if loaded_name not in requested:
                self.disable(loaded_name, persist=False)
        self.discover()
        self._enabled_names = set(requested)
        loaded: list[PluginInfo] = []
        for name in requested:
            if name not in self._metadata:
                self._diagnose(
                    "PLUGIN_ENABLED_MISSING",
                    name,
                    "snapshotted plugin is missing or invalid",
                )
                continue
            try:
                loaded.append(self.enable(name, persist=False))
            except PluginError:
                continue
        return tuple(loaded)

    def close(self) -> None:
        """Unload runtime modules and tools without changing persisted trust."""

        for name in tuple(self._loaded_modules):
            self.disable(name, persist=False)

    def _read_enabled_state(self) -> tuple[str, ...]:
        if not self.state_path.exists():
            return ()
        try:
            document = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            self._diagnose(
                "PLUGIN_STATE_INVALID", None, "plugin enabled state is invalid"
            )
            return ()
        valid = (
            type(document) is dict
            and set(document) == {"schema_version", "enabled"}
            and document.get("schema_version") == 1
            and type(document.get("enabled")) is list
            and all(
                type(name) is str and _PLUGIN_NAME.fullmatch(name) is not None
                for name in document.get("enabled", ())
            )
            and len(set(document.get("enabled", ())))
            == len(document.get("enabled", ()))
        )
        if not valid:
            self._diagnose(
                "PLUGIN_STATE_INVALID", None, "plugin enabled state is invalid"
            )
            return ()
        return tuple(sorted(document["enabled"]))

    def _write_enabled_state(self) -> None:
        text = json.dumps(
            {
                "schema_version": 1,
                "enabled": sorted(self._enabled_names),
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        _atomic_write_text(self.state_path, text + "\n")

    def _diagnose(
        self, code: str, plugin_name: str | None, message: str
    ) -> None:
        self._diagnostics = self._diagnostics + (
            PluginDiagnostic(code, plugin_name, message),
        )


def _read_manifest(
    package: Path,
) -> tuple[PluginMetadata | None, PluginDiagnostic | None]:
    manifest = package / "plugin.json"
    if manifest.is_symlink():
        return None, PluginDiagnostic(
            "PLUGIN_PATH_UNSAFE",
            package.name,
            "plugin manifest path is unsafe",
        )
    if not manifest.is_file():
        return None, PluginDiagnostic(
            "PLUGIN_MANIFEST_INVALID",
            package.name,
            "plugin manifest is missing or invalid",
        )
    try:
        document = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None, PluginDiagnostic(
            "PLUGIN_MANIFEST_INVALID",
            package.name,
            "plugin manifest is invalid",
        )
    if not _valid_manifest(document):
        return None, PluginDiagnostic(
            "PLUGIN_MANIFEST_INVALID",
            _safe_manifest_name(document),
            "plugin manifest is invalid",
        )

    entrypoint = document["entrypoint"]
    entrypoint_path = Path(entrypoint)
    if (
        entrypoint_path.is_absolute()
        or ".." in entrypoint_path.parts
        or entrypoint_path.suffix != ".py"
    ):
        return None, PluginDiagnostic(
            "PLUGIN_PATH_UNSAFE",
            document["name"],
            "plugin entrypoint path is unsafe",
        )
    target = package / entrypoint_path
    if _contains_symlink(package, target):
        return None, PluginDiagnostic(
            "PLUGIN_PATH_UNSAFE",
            document["name"],
            "plugin entrypoint path is unsafe",
        )
    if not target.is_file():
        return None, PluginDiagnostic(
            "PLUGIN_ENTRYPOINT_MISSING",
            document["name"],
            "plugin entrypoint is missing",
        )
    try:
        package_root = package.resolve(strict=True)
        resolved_target = target.resolve(strict=True)
    except OSError:
        return None, PluginDiagnostic(
            "PLUGIN_ENTRYPOINT_MISSING",
            document["name"],
            "plugin entrypoint is missing",
        )
    if not resolved_target.is_relative_to(package_root):
        return None, PluginDiagnostic(
            "PLUGIN_PATH_UNSAFE",
            document["name"],
            "plugin entrypoint path is unsafe",
        )
    return (
        PluginMetadata(
            document["name"],
            document["version"].strip(),
            document["description"].strip(),
            entrypoint,
            package_root,
        ),
        None,
    )


def _valid_manifest(document: Any) -> bool:
    if type(document) is not dict or set(document) != _MANIFEST_FIELDS:
        return False
    name = document.get("name")
    version = document.get("version")
    description = document.get("description")
    entrypoint = document.get("entrypoint")
    return (
        type(name) is str
        and _PLUGIN_NAME.fullmatch(name) is not None
        and type(version) is str
        and 0 < len(version.strip()) <= 64
        and type(description) is str
        and 0 < len(description.strip()) <= 500
        and type(entrypoint) is str
        and 0 < len(entrypoint) <= 240
    )


def _safe_manifest_name(document: Any) -> str | None:
    if type(document) is not dict:
        return None
    name = document.get("name")
    return name if type(name) is str and _PLUGIN_NAME.fullmatch(name) else None


def _contains_symlink(package: Path, target: Path) -> bool:
    try:
        relative = target.relative_to(package)
    except ValueError:
        return True
    current = package
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            return True
    return False


def _module_name(name: str, entrypoint: Path) -> str:
    digest = hashlib.sha256(str(entrypoint).encode("utf-8")).hexdigest()[:12]
    nonce = secrets.token_hex(6)
    safe_name = name.replace("-", "_")
    return f"_coding_agent_plugin_{safe_name}_{digest}_{nonce}"

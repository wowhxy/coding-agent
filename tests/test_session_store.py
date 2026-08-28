import hashlib
import json
import os
import re
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from coding_agent.protocol import Message, Role
from coding_agent.session import (
    SessionError,
    SessionRecord,
    deserialize_session,
    serialize_session,
)
from coding_agent.session_store import (
    JsonSessionStore,
    generate_session_id,
    resolve_session_home,
)
import coding_agent.session_store as session_store


CREATED = datetime(2026, 8, 27, 13, 0, tzinfo=timezone.utc)
UPDATED = datetime(2026, 8, 27, 13, 5, tzinfo=timezone.utc)


def clock(values: list[datetime]):
    iterator = iter(values)
    return lambda: next(iterator)


def with_messages(record, text: str = "hello"):
    return replace(record, messages=(Message(Role.USER, text),))


def assert_error(code: str, operation) -> SessionError:
    with pytest.raises(SessionError) as raised:
        operation()
    assert raised.value.error_code == code
    assert "Traceback" not in str(raised.value)
    return raised.value


def workspace_hash(workspace: Path) -> str:
    value = str(workspace.resolve())
    if os.name == "nt":
        value = os.path.normcase(value)
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@pytest.mark.parametrize(
    ("platform", "environ", "home_tail"),
    [
        ("win32", {"LOCALAPPDATA": "C:/isolated/local"}, None),
        ("darwin", {}, Path("Library/Application Support/coding-agent")),
        ("linux", {"XDG_DATA_HOME": "/isolated/xdg"}, None),
        ("linux", {}, Path(".local/share/coding-agent")),
    ],
)
def test_resolve_session_home_uses_platform_fallbacks(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    platform: str,
    environ: dict[str, str],
    home_tail: Path | None,
) -> None:
    fake_home = tmp_path / "isolated-home"
    monkeypatch.setattr(session_store.sys, "platform", platform)
    monkeypatch.setattr(session_store.Path, "home", staticmethod(lambda: fake_home))

    actual = resolve_session_home(environ)

    if platform == "win32":
        expected = Path(environ["LOCALAPPDATA"]) / "coding-agent"
    elif "XDG_DATA_HOME" in environ:
        expected = Path(environ["XDG_DATA_HOME"]) / "coding-agent"
    else:
        assert home_tail is not None
        expected = fake_home / home_tail
    assert actual == expected


def test_resolve_session_home_override_is_the_root_itself(tmp_path: Path) -> None:
    override = tmp_path / "exact-root"

    assert resolve_session_home(
        {
            "CODING_AGENT_HOME": str(override),
            "LOCALAPPDATA": str(tmp_path / "ignored-local"),
            "XDG_DATA_HOME": str(tmp_path / "ignored-xdg"),
        }
    ) == override


def test_generated_session_ids_are_twelve_lowercase_hex() -> None:
    assert all(re.fullmatch(r"[0-9a-f]{12}", generate_session_id()) for _ in range(20))


def test_create_session_is_canonical_in_memory_only_and_retries_collision(tmp_path: Path) -> None:
    root = tmp_path / "store"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    collision = root / "sessions" / "111111111111.json"
    collision.parent.mkdir(parents=True)
    collision.write_text("existing", encoding="utf-8")
    ids = iter(("111111111111", "222222222222"))
    store = JsonSessionStore(root, clock=lambda: CREATED, id_generator=lambda: next(ids))
    before = {path.relative_to(root) for path in root.rglob("*")}

    record = store.create_session(workspace / ".", "provider", "model")

    assert record.session_id == "222222222222"
    assert record.workspace == workspace.resolve()
    assert record.provider == "provider"
    assert record.model == "model"
    assert record.created_at is CREATED
    assert record.updated_at is CREATED
    assert record.messages == ()
    assert {path.relative_to(root) for path in root.rglob("*")} == before


def test_create_session_writes_no_root_artifact_for_fresh_store(tmp_path: Path) -> None:
    root = tmp_path / "absent-store"
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    JsonSessionStore(root, clock=lambda: CREATED, id_generator=lambda: "012345abcdef").create_session(
        workspace, "provider", "model"
    )

    assert not root.exists()


@pytest.mark.parametrize("root_kind", ("workspace", "nested"))
@pytest.mark.parametrize(
    "operation",
    ("create", "load_latest", "load_session", "save"),
)
def test_store_rejects_session_root_equal_to_or_inside_workspace_before_io(
    tmp_path: Path,
    root_kind: str,
    operation: str,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    root = workspace if root_kind == "workspace" else workspace / "session-data"
    store = JsonSessionStore(
        root,
        clock=lambda: CREATED,
        id_generator=lambda: "012345abcdef",
    )
    record = SessionRecord(
        session_id="012345abcdef",
        workspace=workspace.resolve(),
        provider="provider",
        model="model",
        created_at=CREATED,
        updated_at=CREATED,
        messages=(Message(Role.USER, "task"),),
    )
    operations = {
        "create": lambda: store.create_session(workspace, "provider", "model"),
        "load_latest": lambda: store.load_latest(workspace),
        "load_session": lambda: store.load_session("012345abcdef", workspace),
        "save": lambda: store.save(record),
    }

    error = assert_error("SESSION_IO_ERROR", operations[operation])

    assert error.message == "session storage root must be outside workspace"
    assert not (root / "sessions").exists()
    assert not (root / "workspaces").exists()


def test_store_resolves_existing_symlink_components_before_root_containment_check(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    alias = tmp_path / "workspace-alias"
    try:
        alias.symlink_to(workspace, target_is_directory=True)
    except OSError:
        pytest.skip("creating directory symlinks is unavailable in this environment")
    store = JsonSessionStore(
        alias / "session-data",
        clock=lambda: CREATED,
        id_generator=lambda: "012345abcdef",
    )

    error = assert_error(
        "SESSION_IO_ERROR",
        lambda: store.create_session(workspace, "provider", "model"),
    )

    assert error.message == "session storage root must be outside workspace"
    assert store.root == workspace / "session-data"
    assert not (workspace / "session-data").exists()


def test_store_allows_workspace_beneath_an_external_session_root(tmp_path: Path) -> None:
    root = tmp_path / "session-home"
    workspace = root / "checked-out-workspace"
    workspace.mkdir(parents=True)
    store = JsonSessionStore(
        root,
        clock=clock([CREATED, UPDATED]),
        id_generator=lambda: "012345abcdef",
    )

    saved = store.save(
        with_messages(store.create_session(workspace, "provider", "model"))
    )

    assert store.load_latest(workspace) == saved


def test_create_session_rejects_bad_id_and_non_directory_workspace(tmp_path: Path) -> None:
    store = JsonSessionStore(tmp_path / "store", clock=lambda: CREATED, id_generator=lambda: "BAD")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    assert_error("SESSION_SAVE_FAILED", lambda: store.create_session(workspace, "provider", "model"))

    missing = tmp_path / "missing"
    assert_error("SESSION_IO_ERROR", lambda: store.create_session(missing, "provider", "model"))


def test_create_session_stops_after_one_hundred_id_collisions(tmp_path: Path) -> None:
    root = tmp_path / "store"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    collision = root / "sessions" / "111111111111.json"
    collision.parent.mkdir(parents=True)
    collision.write_text("existing", encoding="utf-8")
    attempts = 0

    def colliding_id() -> str:
        nonlocal attempts
        attempts += 1
        if attempts > 100:
            raise AssertionError("session id collision retries exceeded the limit")
        return "111111111111"

    store = JsonSessionStore(root, clock=lambda: CREATED, id_generator=colliding_id)

    assert_error(
        "SESSION_SAVE_FAILED", lambda: store.create_session(workspace, "provider", "model")
    )
    assert attempts == 100
    assert collision.read_text(encoding="utf-8") == "existing"


@pytest.mark.parametrize("failure", [StopIteration(), RuntimeError("host-detail-secret")])
def test_create_session_translates_id_generator_failure(
    tmp_path: Path, failure: Exception
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    def fail_id() -> str:
        raise failure

    store = JsonSessionStore(tmp_path / "store", clock=lambda: CREATED, id_generator=fail_id)

    error = assert_error(
        "SESSION_SAVE_FAILED", lambda: store.create_session(workspace, "provider", "model")
    )
    assert "host-detail-secret" not in str(error)


def test_save_layout_index_schema_and_loading_across_workspaces(tmp_path: Path) -> None:
    root = tmp_path / "store"
    workspace_a = tmp_path / "workspace-a"
    workspace_b = tmp_path / "workspace-b"
    workspace_a.mkdir()
    workspace_b.mkdir()
    ids = iter(("111111111111", "222222222222", "333333333333"))
    times = clock([CREATED, CREATED, CREATED, UPDATED, UPDATED, UPDATED])
    store = JsonSessionStore(root, clock=times, id_generator=lambda: next(ids))
    first = store.create_session(workspace_a, "p1", "m1")
    second = store.create_session(workspace_a, "p2", "m2")
    other = store.create_session(workspace_b, "p3", "m3")

    saved_first = store.save(with_messages(first, "first"))
    saved_second = store.save(with_messages(second, "second"))
    saved_other = store.save(with_messages(other, "other"))

    assert saved_first.created_at is CREATED and saved_first.updated_at is UPDATED
    assert (root / "sessions" / "111111111111.json").is_file()
    assert (root / "sessions" / "222222222222.json").is_file()
    assert (root / "sessions" / "333333333333.json").is_file()
    index_path = root / "workspaces" / f"{workspace_hash(workspace_a)}.json"
    assert len(index_path.stem) == 64
    index = json.loads(index_path.read_text(encoding="utf-8"))
    assert index == {
        "schema_version": 1,
        "workspace": str(workspace_a.resolve()),
        "latest_session_id": "222222222222",
        "session_ids": ["111111111111", "222222222222"],
    }
    assert store.load_latest(workspace_a) == saved_second
    assert store.load_session("111111111111", workspace_a) == saved_first
    assert store.load_latest(workspace_b) == saved_other
    assert saved_first.provider == "p1" and saved_first.model == "m1"
    assert saved_first.messages == (Message(Role.USER, "first"),)


def test_resaving_old_session_moves_it_to_latest_without_duplicate(tmp_path: Path) -> None:
    root = tmp_path / "store"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    ids = iter(("111111111111", "222222222222"))
    store = JsonSessionStore(
        root,
        clock=clock([CREATED, CREATED, UPDATED, UPDATED, UPDATED]),
        id_generator=lambda: next(ids),
    )
    first = store.create_session(workspace, "p", "m")
    second = store.create_session(workspace, "p", "m")
    store.save(with_messages(first, "first"))
    store.save(with_messages(second, "second"))

    saved_again = store.save(with_messages(first, "first revised"))

    index_path = root / "workspaces" / f"{workspace_hash(workspace)}.json"
    index = json.loads(index_path.read_text(encoding="utf-8"))
    assert index["session_ids"] == ["222222222222", "111111111111"]
    assert index["latest_session_id"] == "111111111111"
    assert store.load_latest(workspace) == saved_again


def test_load_latest_returns_none_only_when_index_is_absent(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = JsonSessionStore(tmp_path / "store")

    assert store.load_latest(workspace) is None

    index_path = store.root / "workspaces" / f"{workspace_hash(workspace)}.json"
    index_path.parent.mkdir(parents=True)
    index_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "workspace": str(workspace.resolve()),
                "latest_session_id": "111111111111",
                "session_ids": ["111111111111"],
            }
        ),
        encoding="utf-8",
    )
    assert_error("SESSION_NOT_FOUND", lambda: store.load_latest(workspace))


def test_explicit_load_validates_id_and_requested_workspace(tmp_path: Path) -> None:
    root = tmp_path / "store"
    workspace = tmp_path / "workspace"
    other = tmp_path / "other"
    workspace.mkdir()
    other.mkdir()
    store = JsonSessionStore(root, clock=clock([CREATED, UPDATED]), id_generator=lambda: "111111111111")
    record = store.create_session(workspace, "p", "m")
    store.save(with_messages(record))

    assert_error("SESSION_NOT_FOUND", lambda: store.load_session("../bad", workspace))
    assert_error("SESSION_NOT_FOUND", lambda: store.load_session("222222222222", workspace))
    assert_error("SESSION_WORKSPACE_MISMATCH", lambda: store.load_session(record.session_id, other))


def test_load_rejects_session_document_id_that_does_not_match_requested_filename(
    tmp_path: Path,
) -> None:
    root = tmp_path / "store"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    requested_id = "111111111111"
    stored_id = "222222222222"
    stored = SessionRecord(
        session_id=stored_id,
        workspace=workspace.resolve(),
        provider="provider",
        model="model",
        created_at=CREATED,
        updated_at=UPDATED,
        messages=(Message(Role.USER, "task"),),
    )
    session_path = root / "sessions" / f"{requested_id}.json"
    session_path.parent.mkdir(parents=True)
    session_path.write_text(serialize_session(stored), encoding="utf-8")
    index_path = root / "workspaces" / f"{workspace_hash(workspace)}.json"
    index_path.parent.mkdir(parents=True)
    index_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "workspace": str(workspace.resolve()),
                "latest_session_id": requested_id,
                "session_ids": [requested_id],
            }
        ),
        encoding="utf-8",
    )
    store = JsonSessionStore(root)

    explicit_error = assert_error(
        "SESSION_CORRUPT",
        lambda: store.load_session(requested_id, workspace),
    )
    latest_error = assert_error(
        "SESSION_CORRUPT",
        lambda: store.load_latest(workspace),
    )

    assert explicit_error.message == "session identifier does not match requested session"
    assert latest_error.message == "session identifier does not match requested session"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda doc: doc.update({"unexpected": True}),
        lambda doc: doc.update({"schema_version": 2}),
        lambda doc: doc.update({"workspace": "relative"}),
        lambda doc: doc.update({"workspace": "WRONG-ABSOLUTE"}),
        lambda doc: doc.update({"latest_session_id": "BAD"}),
        lambda doc: doc.update({"session_ids": ["111111111111", "111111111111"]}),
        lambda doc: doc.update({"session_ids": ["222222222222"]}),
    ],
)
def test_load_latest_rejects_malformed_or_wrong_workspace_index(
    tmp_path: Path, mutate
) -> None:
    root = tmp_path / "store"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    document = {
        "schema_version": 1,
        "workspace": str(workspace.resolve()),
        "latest_session_id": "111111111111",
        "session_ids": ["111111111111"],
    }
    mutate(document)
    if document["workspace"] == "WRONG-ABSOLUTE":
        document["workspace"] = str((tmp_path / "other").resolve())
    index_path = root / "workspaces" / f"{workspace_hash(workspace)}.json"
    index_path.parent.mkdir(parents=True)
    index_path.write_text(json.dumps(document), encoding="utf-8")

    assert_error("SESSION_INDEX_CORRUPT", lambda: JsonSessionStore(root).load_latest(workspace))


def test_load_latest_rejects_malformed_index_json(tmp_path: Path) -> None:
    root = tmp_path / "store"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    index_path = root / "workspaces" / f"{workspace_hash(workspace)}.json"
    index_path.parent.mkdir(parents=True)
    index_path.write_text("{", encoding="utf-8")

    assert_error("SESSION_INDEX_CORRUPT", lambda: JsonSessionStore(root).load_latest(workspace))


@pytest.mark.parametrize("constant", ("NaN", "Infinity", "-Infinity"))
def test_load_latest_rejects_nonstandard_json_constants_before_duplicate_key_overwrite(
    tmp_path: Path,
    constant: str,
) -> None:
    root = tmp_path / "store"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = JsonSessionStore(
        root,
        clock=clock([CREATED, UPDATED]),
        id_generator=lambda: "111111111111",
    )
    store.save(with_messages(store.create_session(workspace, "provider", "model")))
    index_path = root / "workspaces" / f"{workspace_hash(workspace)}.json"
    saved_index = index_path.read_text(encoding="utf-8")
    nonstandard_index = saved_index.replace(
        '"schema_version": 1,',
        f'"schema_version": {constant}, "schema_version": 1,',
        1,
    )
    assert nonstandard_index != saved_index
    index_path.write_text(nonstandard_index, encoding="utf-8")

    assert_error("SESSION_INDEX_CORRUPT", lambda: store.load_latest(workspace))


def test_session_codec_errors_are_preserved_by_explicit_load(tmp_path: Path) -> None:
    root = tmp_path / "store"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    sessions = root / "sessions"
    sessions.mkdir(parents=True)
    store = JsonSessionStore(root)

    (sessions / "111111111111.json").write_text("{", encoding="utf-8")
    assert_error("SESSION_CORRUPT", lambda: store.load_session("111111111111", workspace))

    (sessions / "222222222222.json").write_text(
        json.dumps({"schema_version": 2, "future": True}), encoding="utf-8"
    )
    assert_error("SESSION_VERSION_UNSUPPORTED", lambda: store.load_session("222222222222", workspace))


def test_invalid_utf8_session_is_concise_corrupt_error(tmp_path: Path) -> None:
    root = tmp_path / "store"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    session_path = root / "sessions" / "111111111111.json"
    session_path.parent.mkdir(parents=True)
    session_path.write_bytes(b"\xffhost-detail-secret")

    error = assert_error(
        "SESSION_CORRUPT", lambda: JsonSessionStore(root).load_session("111111111111", workspace)
    )
    assert "host-detail-secret" not in str(error)
    assert "invalid start byte" not in str(error)


def test_invalid_utf8_index_is_concise_index_corrupt_error(tmp_path: Path) -> None:
    root = tmp_path / "store"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    index_path = root / "workspaces" / f"{workspace_hash(workspace)}.json"
    index_path.parent.mkdir(parents=True)
    index_path.write_bytes(b"\xffhost-detail-secret")

    error = assert_error(
        "SESSION_INDEX_CORRUPT", lambda: JsonSessionStore(root).load_latest(workspace)
    )
    assert "host-detail-secret" not in str(error)
    assert "invalid start byte" not in str(error)


def test_read_filesystem_error_is_concise_io_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = JsonSessionStore(tmp_path / "store")
    secret = "host-detail-secret"

    def denied(*_args, **_kwargs):
        raise PermissionError(secret)

    monkeypatch.setattr(session_store.Path, "read_text", denied)
    error = assert_error("SESSION_IO_ERROR", lambda: store.load_session("111111111111", workspace))
    assert secret not in str(error)


def test_session_replace_failure_preserves_target_and_cleans_only_its_temp(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "store"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = JsonSessionStore(
        root,
        clock=clock([CREATED, UPDATED, UPDATED]),
        id_generator=lambda: "111111111111",
    )
    record = store.create_session(workspace, "p", "m")
    saved = store.save(with_messages(record, "old"))
    target = root / "sessions" / "111111111111.json"
    old_bytes = target.read_bytes()
    unrelated = target.parent / "unrelated.tmp"
    unrelated.write_bytes(b"keep")
    replaced_sources: list[Path] = []

    def fail_replace(source, destination):
        replaced_sources.append(Path(source))
        raise PermissionError("host-detail-secret")

    monkeypatch.setattr(session_store.os, "replace", fail_replace)

    error = assert_error(
        "SESSION_SAVE_FAILED",
        lambda: store.save(replace(saved, messages=(Message(Role.USER, "new"),))),
    )

    assert error.message == "session could not be saved"
    assert "host-detail-secret" not in str(error)
    assert target.read_bytes() == old_bytes
    assert unrelated.read_bytes() == b"keep"
    assert len(replaced_sources) == 1
    assert not replaced_sources[0].exists()
    assert {path.name for path in target.parent.iterdir()} == {target.name, unrelated.name}


def test_index_replace_failure_leaves_new_session_readable_and_old_index_unchanged(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "store"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    ids = iter(("111111111111", "222222222222"))
    store = JsonSessionStore(
        root,
        clock=clock([CREATED, UPDATED, CREATED, UPDATED]),
        id_generator=lambda: next(ids),
    )
    first = store.create_session(workspace, "p", "m")
    store.save(with_messages(first, "first"))
    index_path = root / "workspaces" / f"{workspace_hash(workspace)}.json"
    old_index = index_path.read_bytes()
    second = store.create_session(workspace, "p2", "m2")
    real_atomic_write = session_store._atomic_write_text
    calls: list[Path] = []

    def fail_index(path: Path, text: str) -> None:
        calls.append(path)
        if path.parent.name == "workspaces":
            raise PermissionError("host-detail-secret")
        real_atomic_write(path, text)

    monkeypatch.setattr(session_store, "_atomic_write_text", fail_index)

    error = assert_error("SESSION_SAVE_FAILED", lambda: store.save(with_messages(second, "second")))

    assert [path.parent.name for path in calls] == ["sessions", "workspaces"]
    assert error.message == "session was saved but workspace index was not updated"
    assert index_path.read_bytes() == old_index
    persisted = deserialize_session((root / "sessions" / "222222222222.json").read_text(encoding="utf-8"))
    assert persisted.messages == (Message(Role.USER, "second"),)

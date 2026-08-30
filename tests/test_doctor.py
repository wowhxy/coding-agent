from __future__ import annotations

from pathlib import Path

import coding_agent.application.diagnostics as diagnostics
from coding_agent.application.diagnostics import render_doctor, run_doctor


SECRET = "doctor-must-not-print-this-secret"


def test_doctor_reports_product_readiness_without_exposing_credential(
    monkeypatch, tmp_path: Path
) -> None:
    workspace = tmp_path / "workspace"
    home = tmp_path / "home"
    workspace.mkdir()
    skill = home / "skills" / "tdd"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: tdd\ndescription: Test first.\n---\n\nTest first.\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(diagnostics.shutil, "which", lambda name: "git.exe" if name == "git" else None)

    checks = run_doctor(
        workspace=workspace,
        provider="deepseek",
        model="deepseek-v4-flash",
        base_url="https://api.deepseek.com",
        api_key_env="DEEPSEEK_API_KEY",
        environ={"DEEPSEEK_API_KEY": SECRET},
        session_home=home,
    )
    output = render_doctor(checks)

    assert all(item.ok for item in checks)
    assert "Python" in output
    assert "Workspace" in output
    assert "Credential" in output and "configured" in output
    assert "1 available" in output
    assert "Ready" in output
    assert SECRET not in output
    assert not any(path.name.startswith(".doctor-") for path in home.rglob("*"))


def test_doctor_missing_configuration_is_actionable_and_not_ready(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    checks = run_doctor(
        workspace=workspace,
        provider="custom",
        model=None,
        base_url=None,
        api_key_env="OPENAI_API_KEY",
        environ={},
        session_home=tmp_path / "home",
    )
    output = render_doctor(checks)

    assert any(item.name == "Provider" and not item.ok for item in checks)
    assert any(item.name == "Credential" and not item.ok for item in checks)
    assert "missing" in output
    assert "Not ready" in output


def test_doctor_handles_unusable_workspace_without_traceback(tmp_path: Path) -> None:
    missing = tmp_path / "missing"

    checks = run_doctor(
        workspace=missing,
        provider="deepseek",
        model="m",
        base_url="https://example.test",
        api_key_env="KEY",
        environ={"KEY": "configured"},
        session_home=tmp_path / "home",
    )

    workspace_check = next(item for item in checks if item.name == "Workspace")
    assert workspace_check.ok is False
    assert str(missing) in workspace_check.detail


def test_doctor_rejects_session_home_inside_workspace_without_probe_artifact(
    monkeypatch, tmp_path: Path
) -> None:
    workspace = tmp_path / "workspace"
    home = workspace / ".product-home"
    workspace.mkdir()
    monkeypatch.setattr(diagnostics.shutil, "which", lambda _name: "git.exe")

    checks = run_doctor(
        workspace=workspace,
        provider="deepseek",
        model="m",
        base_url="https://example.test",
        api_key_env="KEY",
        environ={"KEY": "configured"},
        session_home=home,
    )

    storage = [item for item in checks if "storage" in item.name.casefold()]
    assert storage and all(not item.ok for item in storage)
    assert all("outside workspace" in item.detail for item in storage)
    assert not home.exists()

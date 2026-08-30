from scripts.live_tui_verify import _max_steps, _verification_subprocess_environment


def test_parallel_live_smoke_uses_production_step_budget() -> None:
    assert _max_steps("coding") == 12
    assert _max_steps("subagent") == 20


def test_independent_verification_subprocess_does_not_inherit_provider_key() -> None:
    environment = _verification_subprocess_environment(
        {"DEEPSEEK_API_KEY": "not-a-real-key", "PATH": "safe"}
    )

    assert environment == {"PATH": "safe"}

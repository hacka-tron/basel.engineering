import unused_module_for_ruff_to_flag  # noqa: F401 disabled intentionally below


def test_intentionally_fails_to_verify_ci_gate():
    assert False, "intentional failure to verify the CI gate blocks bad PRs"

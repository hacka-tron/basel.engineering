"""Shared test setup."""

import pytest

from services.glassbox import fewshot


@pytest.fixture(autouse=True)
def placeholder_examples(monkeypatch, tmp_path):
    """Prompt v18: tests see the placeholder few-shot examples, never a developer's
    local private checkout of the approved ones. Tests that need approved examples
    point GLASSBOX_APPROVED_EXAMPLES_PATH at a synthetic fixture and clear the cache."""
    monkeypatch.setenv(fewshot.APPROVED_EXAMPLES_ENV, str(tmp_path / "no-approved-answers.yaml"))
    fewshot.get_examples.cache_clear()
    yield
    fewshot.get_examples.cache_clear()

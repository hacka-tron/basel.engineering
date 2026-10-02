"""Provider-specific token patterns in the ingest secret scanner.

Fixtures are assembled by concatenation so this file itself contains no
token-shaped literal (the repo's own pre-commit and the ingest scanner stay quiet).
"""

import pytest

from services.glassbox.ingest.scanner import secret_reason

A36 = "aB3dE5gH7jK9" + "mN1pQ3sT5vW7" + "yZ9bC1dE3fG5"  # 36 mixed alphanumerics
A40 = A36 + "x7Y9"

POSITIVE = {
    "github-pat-classic": "token = " + "ghp" + "_" + A36,
    "github-oauth": "x " + "gho" + "_" + A36,
    "github-user": "x " + "ghu" + "_" + A36,
    "github-server": "x " + "ghs" + "_" + A36,
    "github-refresh": "x " + "ghr" + "_" + A36,
    "github-fine-grained": "x " + "github" + "_pat_" + A36 + "_" + A36,
    "slack-bot": "x " + "xox" + "b-" + "1234567890-0987654321-" + "abcdEFGH",
    "slack-user": "x " + "xox" + "p-" + "1234567890-0987654321",
    "slack-app": "x " + "xapp" + "-1-" + "A0123456789-" + "9876543210",
    "slack-webhook": "https://hooks." + "slack.com/services/" + "T0123ABCD/B0123ABCD/" + A36[:24],
    "anthropic": "k: " + "sk-" + "ant-" + "api03-" + A40,
    "openai": "k: " + "sk-" + A40 + "ABCD",
    "openai-proj": "k: " + "sk-" + "proj-" + A40,
    "google": "key=" + "AI" + "za" + A36[:35],
    "stripe-secret": "k: " + "sk" + "_live_" + A36[:24],  # pragma: allowlist secret
    "stripe-restricted": "k: " + "rk" + "_live_" + A36[:24],
    "jwt": "t "
    + "eyJ"
    + "hbGciOiJIUzI1NiJ9"
    + ".eyJ"
    + "zdWIiOiIxMjM0NTY3ODkwIn0"
    + ".sig"
    + A36[:20],
    "bearer": "Authorization: " + "Bearer " + A36,
}

NEGATIVE = {
    "github-prefix-only": "set the ghp_ token in settings",
    "github-too-short": "ghp" + "_" + "abc123",
    "github-wrong-letter": "ghx" + "_" + A36,
    "slack-wrong-letter": "xoxz" + "-" + "1234567890-0987654321",
    "slack-short": "xox" + "b-" + "123",
    "slack-webhook-docs": "https://hooks." + "slack.com/services/ is the webhook base",
    "anthropic-prefix-only": "keys start with sk-" + "ant-",
    "openai-kebab-identifier": "use sk-" + "some-very-long-kebab-case-identifier-name-here",
    "openai-short": "sk-" + "abc123",
    "google-short": "AI" + "za" + "short",
    "stripe-test-key": "sk" + "_test_" + A36[:24],
    "stripe-prefix-only": "keys look like sk" + "_live_...",
    "jwt-header-only": "eyJ" + "hbGciOiJIUzI1NiJ9" + " is a header",
    "jwt-two-segments": "eyJ" + "hbGciOiJIUzI1NiJ9" + ".eyJ" + "zdWIiOiIxMjM0NTY3ODkwIn0",
    "bearer-placeholder": "Authorization: Bearer <token>",
    "bearer-env-var": 'curl -H "Authorization: Bearer $TOKEN"',
    "bearer-short": "Authorization: Bearer abc123",
    "bearer-prose": "the Bearer authentication-scheme-description-in-words",
}


@pytest.mark.parametrize("name", POSITIVE)
def test_provider_token_is_flagged(name):
    reason = secret_reason("line one\n" + POSITIVE[name] + "\n")
    assert reason is not None
    assert "at line 2" in reason


@pytest.mark.parametrize("name", NEGATIVE)
def test_near_miss_is_not_flagged(name):
    assert secret_reason(NEGATIVE[name]) is None


def test_reason_names_the_provider():
    assert "GitHub token" in secret_reason(POSITIVE["github-pat-classic"])
    assert "Anthropic" in secret_reason(POSITIVE["anthropic"])
    assert "Bearer" in secret_reason(POSITIVE["bearer"])

"""Prompt v21: no contact placeholder reaches an answer prompt.

Nova Lite copied the example's literal "<email>" into a live answer to "Am I
really talking to Basel?" (2026-10-05); the examples now carry the public
contact address, which the personal-data guard already allows.
"""

from services.glassbox import privacy
from services.glassbox.api import ask


def _all_example_text() -> str:
    parts = []
    for value in (
        ask._STRICT_PLACEHOLDER_EXAMPLES,
        ask._CASUAL_PLACEHOLDER_EXAMPLES,
        ask._STRICT_FIXED_EXAMPLES,
        ask._CASUAL_FIXED_EXAMPLES,
        ask._PLAYFUL_PLACEHOLDER_EXAMPLES,
        ask._PRODUCTION_EXAMPLE,
        ask._REAL_ME_EXAMPLE,
    ):
        parts.extend([value] if isinstance(value, str) else value)
    return "\n".join(parts)


def test_prompt_version_is_current():
    assert ask._PROMPT_VERSION == "v22"


def test_no_example_carries_an_email_placeholder():
    assert "<email>" not in _all_example_text()


def test_contact_examples_use_the_public_address():
    assert privacy.PUBLIC_CONTACT_EMAIL in ask._REAL_ME_EXAMPLE
    assert any(privacy.PUBLIC_CONTACT_EMAIL in e for e in ask._STRICT_PLACEHOLDER_EXAMPLES)


def test_the_public_address_passes_the_answer_guard():
    text = f"The flesh-and-blood me is at {privacy.PUBLIC_CONTACT_EMAIL}."
    masked, count = privacy.mask_answer(text)
    assert count == 0
    assert masked == text

"""HI3a: quote-aware secret redaction for quoted-assignment values.

Regression: when a secret value is wrapped in quotes and contains whitespace,
the quoted-assignment regex must consume the entire quoted value (including the
closing quote), not stop at the first interior space and leak the tail.
"""
from __future__ import annotations

import pytest
from kg import conversation as cv


@pytest.mark.parametrize("payload, secret", [
    # JSON-style: value contains a space
    ('api_key="sk live secret"', "sk live secret"),
    ('api_key: "sk live secret"', "sk live secret"),
    # Single quotes
    ("api_key='sk live secret'", "sk live secret"),
    # JSON object variant with interior space
    ('"auth_token":"sk live secret"', "sk live secret"),
    # Whitespace + closing quote at end
    ('token="abcd efgh ijkl"', "abcd efgh ijkl"),
])
def test_quoted_assignment_secret_redacted_with_interior_whitespace(payload, secret):
    conv = cv.parse_conversation([{"role": "user", "content": payload}])
    content = conv.messages[0].content
    assert secret not in content
    # Tail after the opening quote must also not leak
    tail = secret.split(" ", 1)[1] if " " in secret else secret
    assert tail not in content
    assert cv._REDACTION_MARKER in conv.messages[0].content


def test_quoted_assignment_no_trailing_quote_fragment_leak():
    """Closing quote from the original quoted-secret value must not be left behind."""
    conv = cv.parse_conversation([{"role": "user", "content": 'api_key="sk live secret"'}])
    # The raw input had exactly one closing quote; after redaction it must not remain
    assert 'secret"' not in conv.messages[0].content
    assert 'live"' not in conv.messages[0].content

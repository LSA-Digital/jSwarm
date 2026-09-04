"""R6a follow-up: `compliance.sanitizer` must import and redact correctly
without the COM-112 `substrate` dependency, which is enterprise-only
(governance/reporting; see closure-config.yaml's compliance/substrate
ent_marker) and deliberately not part of this repository. Trimmed the
substrate *import* out rather than pulling in the substrate package or
guarding around a broken import -- this is a security-relevant "fail-closed
leak boundary" module, so silently degrading its coverage at import time (a
runtime guard swallowing the exception) would be the wrong kind of
fail-open. The trim is static and documented in the module docstring.

Fix round 2: an empty `_SECRET_PATTERNS` tuple silently dropped three
upstream detectors (bare email/PII, sk-prefixed keys, JWT-shaped tokens)
that have no local equivalent -- restored as ordinary, uncoupled regexes
(no import of substrate), since `sanitize`/`contains_sensitive` are a real
runtime gate (`jswarm/installer/preflight/__init__.py` raises on failure).
"""

from __future__ import annotations

from jswarm.compliance.sanitizer import _SECRET_PATTERNS, contains_sensitive, sanitize


def test_secret_patterns_tuple_carries_the_three_restored_detectors():
    # Not empty: the three upstream detectors with no local equivalent
    # (email/PII, sk- keys, JWT-shaped tokens) are restored here as
    # ordinary regexes -- no import of substrate, just the pattern text.
    assert len(_SECRET_PATTERNS) == 3


def test_bare_email_is_detected_and_redacted():
    assert contains_sensitive("contact keith@example.com for access") is True
    assert sanitize("contact keith@example.com for access") == "contact [REDACTED] for access"


def test_sk_prefixed_key_is_detected_and_redacted():
    token = "sk-" + "a" * 20
    assert contains_sensitive(token) is True
    assert sanitize(token) == "[REDACTED]"
    # Case-insensitive per the upstream pattern.
    assert contains_sensitive("SK-" + "B" * 20) is True


def test_jwt_shaped_token_is_detected_and_redacted():
    jwt = (
        "eyJhbGciOiJIUzI1NiJ9."
        "eyJzdWIiOiIxMjM0NTY3ODkwIn0."
        "dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"
    )
    assert contains_sensitive(jwt) is True
    assert sanitize(jwt) == "[REDACTED]"


def test_local_key_like_token_patterns_still_redact():
    assert sanitize("token=" + "a" * 40) == "[REDACTED]"
    assert contains_sensitive("AKIAABCDEFGHIJKLMNOP") is True
    assert sanitize("ghp_" + "b" * 40) == "[REDACTED]"
    assert sanitize("nothing sensitive here") == "nothing sensitive here"


def test_pem_private_key_block_still_redacted():
    block = "-----BEGIN PRIVATE KEY-----\nMIIB...\n-----END PRIVATE KEY-----"
    assert sanitize(block) == "[REDACTED]"


def test_host_local_path_still_redacted():
    assert sanitize("see /Users/example/secret.txt") == "see [REDACTED]"


def test_sanitize_preserves_container_shape():
    assert sanitize({"a": ["ghp_" + "c" * 40, "clean"]}) == {"a": ["[REDACTED]", "clean"]}

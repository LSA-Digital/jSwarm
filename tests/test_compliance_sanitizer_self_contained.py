"""R6a follow-up: `compliance.sanitizer` must import and redact correctly
without the COM-112 `substrate` dependency, which is enterprise-only
(governance/reporting; see closure-config.yaml's compliance/substrate
ent_marker) and deliberately not part of this repository. Trimmed the
substrate tuple out rather than pulling in the substrate package or guarding
around a broken import -- this is a security-relevant "fail-closed leak
boundary" module, so silently degrading its coverage at import time (a
runtime guard swallowing the exception) would be the wrong kind of
fail-open. The trim is static and documented in the module docstring.
"""

from __future__ import annotations

from jswarm.compliance.sanitizer import _SECRET_PATTERNS, contains_sensitive, sanitize


def test_substrate_patterns_tuple_is_empty_not_missing():
    # The name stays importable (some callers reference it directly) but
    # deliberately carries nothing -- the substrate tuple was never copied.
    assert _SECRET_PATTERNS == ()


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

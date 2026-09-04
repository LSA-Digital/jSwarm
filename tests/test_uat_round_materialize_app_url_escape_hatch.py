"""`app_url: "N/A"` must be accepted for a ticket with no running application.

Found in the same clean-Mac rehearsal (2026-09-04) as the identity-contract
fixes: the plan template's own "UAT state policy" field documents "N/A" as a
legitimate value, but `_validate_app_url` required a real `http`/`https` URL
with a hostname -- a stranger authoring a round for a pure library change (no
app to point at) had to invent a placeholder URL that means nothing, with no
hint in the docs that this was necessary or how.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jswarm.uat_round_materialize import ValidationError, _validate_app_url  # noqa: E402


def test_na_is_accepted_as_the_documented_no_app_placeholder():
    assert _validate_app_url("N/A") == "N/A"


def test_real_urls_still_work():
    assert _validate_app_url("http://localhost:3000") == "http://localhost:3000"


@pytest.mark.parametrize("value", ["not-a-url", "", "ftp://example.com", "http://"])
def test_other_non_url_values_are_still_rejected(value):
    with pytest.raises(ValidationError):
        _validate_app_url(value)

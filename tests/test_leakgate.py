from pathlib import Path
from jswarm.leakgate import scan

# The fixture values below are split with string concatenation so the literal
# forbidden strings never appear contiguous in this file's own source: this
# file is itself scanned by the leak gate, and the whole point of these tests
# is to plant text that the gate must detect in a *scanned target*, not leak
# it from the gate's own test suite.
TICKET = "COM-" + "123"
TICKET_PATTERN = "COM-" + "\\d+"
PRIVATE_PATH = "~/" + "dev" + "/common"
PATH_PATTERN = "dev" + "/common"

def test_finds_ticket_key_and_private_path(tmp_path):
    (tmp_path / "a.md").write_text(f"see {TICKET} and {PRIVATE_PATH}\n")
    (tmp_path / "ok.md").write_text("nothing here\n")
    rules = tmp_path / "leakgate.yaml"
    rules.write_text(f"patterns:\n  - '{TICKET_PATTERN}'\n  - '{PATH_PATTERN}'\n")
    found = scan(tmp_path, rules)
    assert {(f.path, f.pattern) for f in found} == {("a.md", TICKET_PATTERN), ("a.md", PATH_PATTERN)}

def test_skips_git_and_venv(tmp_path):
    (tmp_path / ".venv").mkdir(); (tmp_path / ".venv" / "x.py").write_text(TICKET[:4] + "1")
    rules = tmp_path / "leakgate.yaml"; rules.write_text(f"patterns: ['{TICKET_PATTERN}']\n")
    assert scan(tmp_path, rules) == []

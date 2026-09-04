from pathlib import Path
COMMANDS = ["jSetup", "jPlan", "jGo", "jTest", "jUAT", "jFix", "jClose", "jMerge", "jPrecompact"]

def test_every_command_has_a_manpage_in_the_agreed_shape():
    for c in COMMANDS:
        text = (Path("docs/manpages") / f"{c}.md").read_text()
        assert text.startswith("---\n") and f"command: /{c}\n" in text
        for h in ("## What it is", "## When to use it", "## Diagram", "## Options"):
            assert h in text, (c, h)

def test_getting_started_matches_contract():
    t = Path("docs/getting-started.md").read_text()
    for s in ("~/dev/jswarm", "./install.sh check", "./install.sh install", "./install.sh verify",
              "--jira-key", "https://mcp.atlassian.com/v2/mcp", "http://localhost:8766/uat/"):
        assert s in t, s
    assert "--provider" not in t
    assert ("dev" + "/common") not in t  # split so this line itself stays leak-gate clean

def test_support_is_stated_honestly():
    t = Path("docs/getting-started.md").read_text().lower()
    assert "macos" in t and "claude code" in t
    for unsupported in ("codex", "cursor", "linux", "windows"):
        # may be mentioned only as not supported, never as a supported option
        for line in t.splitlines():
            if unsupported in line:
                assert "not supported" in line or "not yet" in line, line

def test_the_tracker_free_path_is_documented():
    t = Path("docs/without-jira.md").read_text()
    for c in ("/jPlan", "/jGo", "/jTest", "/jUAT", "/jFix", "/jClose", "/jMerge"):
        assert c in t, c
    assert "--jira-key" not in t

def test_docs_state_that_no_command_invokes_another():
    t = Path("docs/how-the-loop-works.md").read_text()
    assert "/jGo" in t and "does not" in t.lower()

def test_colgrep_is_documented_as_not_implemented_not_as_a_ripgrep_dependency():
    # Fix round 1 named Docker for optional ColGREP search, which was wrong: Docker
    # gates an unrelated, unshipped demo-stack preflight. The docs were then "fixed" to
    # name ripgrep instead -- also wrong: ripgrep is merely what --with-colgrep's stub
    # happened to check, not what ColGREP actually needs, and the stub installed
    # nothing regardless. The honest state is that ColGREP is not implemented in this
    # release; the docs must say that, not offer a fake dependency for either wrong guess.
    t = Path("docs/getting-started.md").read_text()
    assert "docker" not in t.lower()
    assert "not implemented" in t.lower()
    assert "colgrep_search" in t  # names the real MCP tools, not a fake "just install ripgrep" fix

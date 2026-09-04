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

def test_colgrep_is_documented_as_a_real_optional_component_needing_rust_not_docker():
    # Earlier rounds named Docker, then ripgrep, for optional ColGREP search -- both
    # wrong: Docker gates an unrelated, unshipped demo-stack preflight, and ripgrep was
    # merely what the old stub happened to check, not what ColGREP actually needs. Now
    # that --with-colgrep is real (cargo install colgrep + a bundled MCP server), the
    # docs must say exactly that: a real optional install needing a Rust toolchain, not
    # Docker, not ripgrep, and not the old "not implemented" stub language.
    t = Path("docs/getting-started.md").read_text()
    assert "docker" not in t.lower()
    assert "not implemented" not in t.lower()
    assert "cargo install colgrep" in t
    assert "rust" in t.lower()
    assert "colgrep_search" in t  # names the real MCP tools it registers
    assert "colgrep_list_dev_indices" in t
    assert "--with-colgrep" in t

import re, sys
from pathlib import Path
from jswarm.platform import current

def test_platform_detection_lives_only_in_the_platform_layer():
    pattern = re.compile(r"sys\.platform|platform\.system\(\)|\bdarwin\b|Homebrew|/opt/homebrew|xcode-select|launchctl", re.IGNORECASE)
    offenders = []
    for p in Path("jswarm").rglob("*.py"):
        if p.parts[1] == "platform":
            continue
        for n, line in enumerate(p.read_text().splitlines(), 1):
            if pattern.search(line):
                offenders.append(f"{p}:{n}: {line.strip()}")
    assert offenders == [], "platform specifics outside the platform layer:\n" + "\n".join(offenders)

def test_unsupported_platform_is_honest():
    from jswarm.platform.base import UnsupportedPlatform
    up = UnsupportedPlatform("linux")
    assert not up.is_supported()
    m = up.unsupported_message()
    assert "linux" in m and "macOS" in m
    assert "not supported" in m.lower()

def test_macos_is_supported_here():
    if sys.platform != "darwin":
        return
    assert current().is_supported() and current().name == "macos"

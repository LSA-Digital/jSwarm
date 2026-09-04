import re
from pathlib import Path
ENT = re.compile(r"nfr[-_]catalog|test[-_]catalog|jinfra|joptimize|lineage|walkthrough|homing_map|catalog_one_home|build_inventory|jmerge_form_events|jDebug|jArchitect")
def test_core_skills_do_not_call_enterprise_machinery():
    for skill in ("jClose", "jMerge", "jFix", "jTest", "jUAT"):
        for p in (Path("skills") / skill).rglob("*.md"):
            for n, line in enumerate(p.read_text().splitlines(), 1):
                assert not ENT.search(line), f"{p}:{n}: {line.strip()}"
        assert f"-m jswarm.ext {skill}" in (Path("skills") / skill / "SKILL.md").read_text()

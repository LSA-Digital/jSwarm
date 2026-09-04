"""adapter for the single Phase-1 effective-digest authority."""
from pathlib import Path
from jswarm.fix_localization import DigestVerdict, compose_effective_bytes as _compose, verify_effective_digest as _verify

def compose_effective_bytes(*, member_path: str, project_root: Path) -> bytes:
    return _compose(manifest_path=member_path, project_root=project_root)

def verify_effective_digest(*, member_path: str, cited_sha256: str, project_root: Path) -> DigestVerdict:
    return _verify(manifest_path=member_path, cited_sha256=cited_sha256, project_root=project_root)

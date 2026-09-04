"""Phase 2 — decision-review UI render wrapper.

The supported entry point for building the portal static site:

  1. Reads a build manifest JSON listing contracts, optional publication
     manifests, and qa-thread fixtures.
  2. Validates every contract/manifest through the jsonschema registry
     (reusing :mod:`jswarm.portal.validate`) and builds the
     normalized view model per contract
     (:mod:`jswarm.portal.view_model`) plus the deterministic
     Markdown projection (:mod:`jswarm.portal.render_markdown`).
  3. Writes ONE canonical build object to ``portal/.tmp-build/
     review-data.canonical.json`` (the DASHBOARD_DATA_FILE-equivalent for
     this app, injected via ``DECISION_REVIEW_DATA_FILE``).
  4. Resolves the approved Node binary (fnm 24.14.0 install dir),
     bootstraps deps with ``npm ci --ignore-scripts`` when ``node_modules``
     is missing, and invokes ``astro build`` in a deterministic-leaning env
     (TZ=UTC, no telemetry).
  5. Mirrors ``portal/dist`` into ``--out``.

CLI:
    .venv/bin/python -m jswarm.portal.render_ui \
        --build-manifest <build-manifest.json> --out <dist-out-dir> [--force-ci]

    portal convenience: --build-only skips --build-manifest/--out and
    builds every auto-discovered example straight into the canonical portal
    dist directory (jswarm/portal/dist), using the same npm ci
    --ignore-scripts / npm run build steps as the path above::

        .venv/bin/python -m jswarm.portal.render_ui --build-only

Build-manifest shape (schema "jswarm.fix-decisions.review-build-input/1")::

    {
      "schema": "jswarm.fix-decisions.review-build-input/1",
      "contracts": [{"contract": "<path>", "manifest": "<path or null>"}, ...],
      "qa_threads": ["<path>", ...]
    }
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from jswarm.portal.render_markdown import render_markdown
from jswarm.portal.view_model import build_view_model, load_qa_threads

REPO_ROOT = Path(__file__).resolve().parents[2]
APP_DIR = REPO_ROOT / "portal"


# duplicated from the npm gate on purpose: hooks run outside the repo
def resolve_fnm_bin_dir() -> str:
    """Resolve the fnm Node bin dir for THIS machine at runtime.

    Order: ``JSWARM_NODE_BIN`` env -> the resolved directory of ``node`` on PATH ->
    the newest ``~/.local/share/fnm/node-versions/*/installation/bin`` -> "" (fail open).
    """
    explicit = os.environ.get("JSWARM_NODE_BIN", "").strip()
    if explicit and (Path(explicit) / "node").exists():
        return explicit
    node = shutil.which("node")
    if node:
        return str(Path(node).resolve().parent)
    base = Path.home() / ".local" / "share" / "fnm" / "node-versions"
    if base.is_dir():
        # Sort by numeric version, not as strings: "v9" sorts after "v24"
        # lexicographically and would hand back an ancient Node.
        def _version_key(p: Path) -> tuple[int, ...]:
            parts = []
            for chunk in p.parent.parent.name.lstrip("v").split("."):
                digits = ""
                for ch in chunk:
                    if not ch.isdigit():
                        break
                    digits += ch
                parts.append(int(digits) if digits else 0)
            return tuple(parts)

        bins = sorted((p for p in base.glob("*/installation/bin") if (p / "node").exists()), key=_version_key)
        if bins:
            return str(bins[-1])
    return ""


NODE_BIN_DIR = Path(resolve_fnm_bin_dir() or "/nonexistent-node-bin")
BUILD_DATA_SCHEMA = "jswarm.fix-decisions.review-build/1"
BUILD_INPUT_SCHEMA = "jswarm.fix-decisions.review-build-input/1"
# Sentinel for dynamic discovery of all positive examples (no manifest file).
_AUTO_DISCOVER = Path("auto")


def _load_build_manifest(path: Path | None) -> dict:
    if path is None or path == _AUTO_DISCOVER:
        return discover_default_build_input()
    document = json.loads(path.read_text(encoding="utf-8"))
    if document.get("schema") != BUILD_INPUT_SCHEMA:
        raise ValueError(
            f"{path}: expected schema {BUILD_INPUT_SCHEMA!r}, got {document.get('schema')!r}"
        )
    entries = document.get("contracts")
    if not isinstance(entries, list) or not entries:
        raise ValueError(f"{path}: 'contracts' must be a non-empty list")
    for entry in entries:
        if not isinstance(entry, dict) or "contract" not in entry:
            raise ValueError(f"{path}: each contracts entry needs a 'contract' path")
    return document


def _slug_for(contract_path: Path, view_model: dict) -> str:
    # Published bundles are addressed permanently by publication id. Bare,
    # unmanifested examples retain their historical contract-derived slugs.
    if view_model["identity"].get("publication_id"):
        return view_model["identity"]["publication_id"]
    slug = view_model["identity"]["contract_slug"] or contract_path.stem
    # Disambiguate same-slug contracts (e.g. defect + fix of one cycle).
    prefix = {"fix-contract": "fix", "defect-contract": "defect"}.get(
        view_model["identity"]["document_type"], view_model["identity"]["document_type"]
    )
    return f"{prefix}-{slug}-{view_model['identity']['contract_version'] or 'v1'}"


def discover_default_build_input() -> dict:
    """Dynamically discover every positive contract + qa-thread example.

    Robust to the Phase-1 schema-hardening lane adding example fixtures
    (e.g. ``fix-contract.demo-617-build-stage-restore.json``): whatever is in
    ``schemas/fix-decisions/examples/`` at build time is what gets rendered.
    Publication manifests are matched to contracts by their
    ``contract_schema`` when the manifest's own contract example is present.
    """
    examples = REPO_ROOT / "schemas" / "fix-decisions" / "examples"
    contracts = []
    for path in sorted(
        list(examples.glob("fix-contract.*.json"))
        + list(examples.glob("defect-contract.*.json"))
    ):
        contracts.append({"contract": str(path.relative_to(REPO_ROOT)), "manifest": None})
    # Attach each publication manifest to its matching contract example.
    # The manifest's contract_path points at the canonical published artifact
    # (often in another repository), so match on the document itself: load
    # each candidate example and compare schema + contract_id.
    for manifest_path in sorted(examples.glob("publication-manifest.*.json")):
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        for entry in contracts:
            example = REPO_ROOT / entry["contract"]
            try:
                document = json.loads(example.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            slug = document.get("contract_slug") or ""
            version = document.get("contract_version") or ""
            if (
                document.get("schema") == manifest.get("contract_schema")
                and slug
                and slug in manifest.get("contract_path", "")
                and version in manifest.get("contract_version", "")
            ):
                entry["manifest"] = str(manifest_path.relative_to(REPO_ROOT))
                break
    qa_threads = [
        str(p.relative_to(REPO_ROOT)) for p in sorted(examples.glob("qa-thread.*.json"))
    ]
    return {
        "schema": BUILD_INPUT_SCHEMA,
        "contracts": contracts,
        "qa_threads": qa_threads,
    }


def build_review_data(build_manifest_path: Path) -> dict:
    """Validate inputs -> canonical review-build data object (deterministic)."""
    build_manifest = _load_build_manifest(build_manifest_path)

    def resolve_build_path(value: str) -> Path:
        path = Path(value)
        return path if path.is_absolute() else REPO_ROOT / path

    qa_paths = [resolve_build_path(p) for p in build_manifest.get("qa_threads", [])]
    qa_threads = load_qa_threads(sorted(qa_paths))

    contracts = []
    for entry in sorted(build_manifest["contracts"], key=lambda e: e["contract"]):
        contract_path = resolve_build_path(entry["contract"])
        manifest_value = entry.get("manifest")
        manifest_path = resolve_build_path(manifest_value) if manifest_value else None
        view_model = build_view_model(contract_path, manifest_path)
        markdown = render_markdown(view_model)
        slug = _slug_for(contract_path, view_model)

        anchored = [
            t
            for t in qa_threads
            if t.get("anchor", {}).get("section_id") in view_model["section_ids"]
        ]
        contracts.append(
            {
                "slug": slug,
                "document_type": view_model["identity"]["document_type"],
                "markdown": markdown,
                "view_model": view_model,
                "qa_threads": anchored,
            }
        )

    return {
        "schema": BUILD_DATA_SCHEMA,
        "generated_from": sorted(
            [e["contract"] for e in build_manifest["contracts"]]
            + [str(p) for p in qa_paths]
        ),
        "qa_threads": qa_threads,
        "contracts": contracts,
    }


def _npm() -> Path:
    if not (NODE_BIN_DIR / "node").exists():
        raise RuntimeError(
            "Node 24 via fnm is required to build the portal; "
            "run: brew install fnm && fnm install 24"
        )
    npm = NODE_BIN_DIR / "npm"
    if not npm.exists():
        raise RuntimeError(f"npm not found at {npm}; broken managed Node install?")
    return npm


def ensure_dependencies(force: bool = False) -> None:
    """Bootstrap deps with ``npm ci --ignore-scripts`` when needed."""
    lockfile = APP_DIR / "package-lock.json"
    if not lockfile.is_file():
        raise RuntimeError(
            "portal/package-lock.json missing; generate it in a clean "
            "checkout via `npm install --package-lock-only --ignore-scripts`."
        )
    if force or not (APP_DIR / "node_modules").is_dir():
        subprocess.run(
            [str(_npm()), "ci", "--ignore-scripts"],
            cwd=APP_DIR,
            check=True,
            env={**os.environ, "PATH": os.pathsep.join([str(NODE_BIN_DIR), os.environ.get("PATH", "")])},
        )


def astro_build(out_dir: Path, work_dir: Path) -> None:
    """Invoke Astro using a transaction-local data/dist work directory."""
    npm = _npm()
    env = os.environ.copy()
    env["PATH"] = os.pathsep.join([str(NODE_BIN_DIR), env.get("PATH", "")])
    env["TZ"] = "UTC"
    env["CI"] = "1"
    env["ASTRO_TELEMETRY_DISABLED"] = "1"
    env["DECISION_REVIEW_DATA_FILE"] = str((work_dir / "review-data.canonical.json").resolve())
    dist = work_dir / "dist"
    if dist.exists():
        shutil.rmtree(dist)
    completed = subprocess.run([str(npm), "run", "build", "--", "--outDir", str(dist)], cwd=APP_DIR, env=env, text=True, capture_output=True)
    if completed.returncode:
        raise RuntimeError(f"astro build failed: {completed.stderr or completed.stdout}")
    if not dist.is_dir() or not any(p.is_file() for p in dist.rglob("*")):
        raise RuntimeError("astro build produced no dist output")
    shutil.copytree(dist, out_dir, dirs_exist_ok=True)


def build_to(build_manifest_path: Path, out_dir: Path, work_dir: Path) -> None:
    """Build one review generation without mutating the global .tmp-build directory."""
    data = build_review_data(build_manifest_path)
    work_dir.mkdir(parents=True, exist_ok=True)
    (work_dir / "review-data.canonical.json").write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    ensure_dependencies()
    out_dir.mkdir(parents=True, exist_ok=True)
    astro_build(out_dir, work_dir)


# The portal's canonical dist location (matches
# deploy/decision-review/config.template.json's "dist_dir").
DEFAULT_PORTAL_DIST_DIR = REPO_ROOT / "jswarm" / "portal" / "dist"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="jswarm.portal.render_ui",
        description="Build the portal static site from validated contracts.",
    )
    parser.add_argument(
        "--build-manifest",
        required=False,
        type=Path,
        default=None,
        help=(
            "review-build-input JSON listing contracts, manifests, and qa-threads; "
            "omit (or pass 'auto') to dynamically discover every positive example "
            "under schemas/fix-decisions/examples/"
        ),
    )
    parser.add_argument("--out", required=False, type=Path, default=None, help="output directory for built static site")
    parser.add_argument(
        "--build-only",
        action="store_true",
        help=(
            "portal convenience: skip requiring --out and build straight into "
            "the canonical portal dist directory (jswarm/portal/dist). Runs "
            "the same npm ci --ignore-scripts / npm run build steps as the normal "
            "path; nothing else changes."
        ),
    )
    parser.add_argument(
        "--force-ci",
        action="store_true",
        help="force `npm ci --ignore-scripts` even if node_modules exists",
    )
    args = parser.parse_args(argv)

    if args.out is None:
        if args.build_only:
            args.out = DEFAULT_PORTAL_DIST_DIR
        else:
            parser.error("--out is required unless --build-only is set")

    build_manifest = args.build_manifest
    if build_manifest is not None and str(build_manifest) == "auto":
        build_manifest = _AUTO_DISCOVER
    try:
        data = build_review_data(build_manifest)
    except (ValueError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    canonical = APP_DIR / ".tmp-build" / "review-data.canonical.json"
    canonical.parent.mkdir(parents=True, exist_ok=True)
    canonical.write_text(
        json.dumps(data, indent=2, sort_keys=False, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    ensure_dependencies(force=args.force_ci)
    args.out.mkdir(parents=True, exist_ok=True)
    astro_build(args.out, APP_DIR / ".tmp-build")
    print(f"rendered: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

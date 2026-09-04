"""COM-389 Phase 2 — normalized view model for fix-decision contracts.

Loads a contract JSON document, validates it against its schema (reusing
:mod:`jswarm.portal.validate` internals), and produces ONE normalized
view-model dict that feeds BOTH projections (Jinja2 Markdown and the Astro
review UI). The view model is deterministic: no timestamps, no random ids —
identical input documents produce identical view models.

Supported document types (auto-detected): ``defect-contract`` and
``fix-contract``. A publication manifest may be supplied to bind the
identity block (canonical contract path + SHA-256 digest).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from jswarm.portal import validate as validate_mod

# Document schema constants (see validate_mod.SCHEMA_CONSTS).
FIX_SCHEMA = "jswarm.fix-decisions.fix-contract/v1"
DEFECT_SCHEMA = "jswarm.fix-decisions.defect-contract/v1"
MANIFEST_SCHEMA = "jswarm.fix-decisions.publication-manifest/v1"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _text(value: Any) -> str:
    """Coerce a text-bearing field to a string ('text' wrapper or bare str)."""
    if isinstance(value, dict):
        return str(value.get("text", ""))
    return "" if value is None else str(value)


def _section_tree(sections: list[dict], depth: int = 1) -> list[dict]:
    """Normalize the recursive section tree, preserving stable section ids."""
    out = []
    for section in sections or []:
        node = {
            "section_id": section["section_id"],
            "title": section.get("title", section["section_id"]),
            "body": section.get("body", ""),
            "depth": depth,
        }
        if section.get("subsections"):
            node["subsections"] = _section_tree(section["subsections"], depth + 1)
        out.append(node)
    return out


def _flatten_sections(tree: list[dict]) -> list[dict]:
    flat: list[dict] = []
    for node in tree:
        flat.append(node)
        flat.extend(_flatten_sections(node.get("subsections", [])))
    return flat


def load_qa_threads(paths: list[Path]) -> list[dict]:
    """Load + AJV-equivalently validate Q&A thread fixtures.

    Each path must be a schema-valid ``jswarm.fix-decisions.qa-thread/v1``
    document (validated through the same jsonschema registry the contract
    validation uses). Returns threads in the given (caller-sorted) order.
    """
    threads = []
    for path in paths:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
        if document.get("schema") != "jswarm.fix-decisions.qa-thread/v1":
            raise ValueError(f"{path}: not a qa-thread/v1 document")
        validator = validate_mod.Draft202012Validator(
            validate_mod.SCHEMAS["qa"], registry=validate_mod.REGISTRY
        )
        errors = list(validator.iter_errors(document))
        if errors:
            location = "/".join(str(p) for p in errors[0].absolute_path) or "<root>"
            raise ValueError(f"{path}: invalid qa-thread at {location}: {errors[0].message}")
        threads.append(document)
    return threads


def build_view_model(
    contract_path: str | Path,
    manifest_path: str | Path | None = None,
    project_root: str | Path | None = None,
) -> dict:
    """Validate a contract (+ optional publication manifest) -> view model."""
    contract_file = Path(contract_path)
    document = json.loads(contract_file.read_text(encoding="utf-8"))
    member = document if document.get("document_type") == "contract-member" else None

    valid, messages = validate_mod.validate(contract_file)
    if not valid:
        raise ValueError(f"invalid contract {contract_file}: " + "; ".join(messages))
    effective_sha = None
    if member is not None:
        from jswarm.portal.compose import verify_effective_digest
        root = Path(project_root) if project_root is not None else contract_file.parent
        cited = member["effective_sha256"]
        verdict = verify_effective_digest(member_path=str(contract_file.relative_to(root)) if contract_file.is_relative_to(root) else str(contract_file), cited_sha256=cited, project_root=root)
        if verdict.status != "verified":
            raise ValueError(f"{verdict.reason_code}: contract member digest blocked")
        effective_sha = verdict.effective_sha256
        document = member["body"]

    manifest = None
    manifest_digest = None
    if manifest_path is not None:
        manifest_file = Path(manifest_path)
        m_valid, m_messages = validate_mod.validate(manifest_file, "manifest")
        if not m_valid:
            raise ValueError(f"invalid manifest {manifest_file}: " + "; ".join(m_messages))
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
        # The manifest digest binds the canonical published artifact at its
        # contract_path (possibly in another repository). A local example copy
        # may legitimately differ, so record both and let the projection
        # surface the mismatch rather than failing here. Enforcement of an
        # exact digest match is the Phase-3 receipt-service concern.
        manifest_digest = manifest.get("contract_sha256")

    is_fix = (member or document)["schema"] == FIX_SCHEMA
    is_defect = (member or document)["schema"] == DEFECT_SCHEMA
    if not (is_fix or is_defect):
        raise ValueError(f"unsupported contract schema {(member or document)['schema']!r}")
    if member is not None and "defects" not in document:
        from jswarm.fix_localization import _contract_chain
        relative = str(contract_file.relative_to(root)) if contract_file.is_relative_to(root) else str(contract_file)
        document = _contract_chain(root, relative)[0]["body"]

    contract_sha = sha256_file(contract_file)
    identity_source = member or document

    # Identity block: exact canonical path + digest (SC-14 discipline).
    identity = {
        "contract_id": identity_source["contract_id"],
        "document_type": identity_source["document_type"],
        "contract_slug": document.get("contract_slug", ""),
        "contract_version": identity_source.get("contract_version", ""),
        "contract_status": document.get("contract_status", ""),
        "source_path": document.get("source_path", ""),
        "schema": identity_source["schema"],
        "schema_version": identity_source.get("schema_version", ""),
        "canonical_path": (manifest.get("contract_path") if manifest else document.get("source_path", "")),
        "sha256": effective_sha or manifest_digest or contract_sha,
        "effective_sha256": effective_sha or manifest_digest or contract_sha,
        "file_sha256": contract_sha,
        "computed_sha256": contract_sha,
        "ticket": identity_source["ticket"],
        "cycle_key": identity_source["cycle_key"],
        "gate_kind": document.get("gate_kind", ""),
        "gate_mode": document.get("gate_mode", ""),
        "publication_id": (manifest or {}).get("publication_id", ""),
        "publication_kind": (manifest or {}).get("publication_kind", ""),
        "issued_by": document.get("issued_by", ""),
        "issued_at": document.get("issued_at", ""),
        "published_at_utc": (manifest or {}).get("published_at_utc", ""),
    }

    scope = document.get("scope", {})
    defects = [
        {
            "key": d["key"],
            "source_key": _text(d.get("source_key")),
            "conviction": _text(d.get("conviction")),
            "authorization": _text(d.get("authorization")),
            "fixed_means": _text(d.get("fixed_means")),
        }
        for d in document.get("defects", [])
    ]
    authorized_defects = [
        {
            "key": d["key"],
            "authorization": _text(d.get("authorization")),
            "fixed_means": _text(d.get("fixed_means")),
            "parent_scope_trace": d.get("parent_scope_trace", {}),
        }
        for d in document.get("authorized_defects", [])
    ]

    sections = _section_tree(document.get("sections", []))
    all_section_ids = [s["section_id"] for s in _flatten_sections(sections)]
    business_header = document.get("business_header", {"blocks": []})
    deltas = []
    if member is not None:
        from jswarm.fix_localization import _contract_chain
        relative = str(contract_file.relative_to(root)) if contract_file.is_relative_to(root) else str(contract_file)
        chain = _contract_chain(root, relative)
        business_header = chain[0]["body"].get("business_header", {"blocks": []})
        deltas = [entry["body"] for entry in chain[1:]]
    lane_anchors = {d.get("key", ""): list(document.get("detail_refs", [])) + list(document.get("evidence_refs", [])) for d in document.get("defects", [])}

    view_model = {
        "identity": identity,
        "is_member": member is not None,
        "business_header": business_header,
        "deltas": deltas,
        "lane_anchors": lane_anchors,
        "decision": _text(document.get("decision")),
        "summary": [ _text(b) for b in document.get("summary", []) ],
        "scope": {
            "in_scope": [ _text(row.get("item")) for row in scope.get("in_scope", []) ],
            "out_of_scope": [
                {
                    "item": _text(row.get("item")),
                    "reason": _text(row.get("reason")),
                }
                for row in scope.get("out_of_scope", [])
            ],
        },
        "defects": defects,
        "authorized_defects": authorized_defects,
        "flow": {
            "representation": document.get("flow", {}).get("representation", "text-diagram"),
            "body": document.get("flow", {}).get("body", ""),
        },
        "risks": [
            {
                "priority": _text(r.get("priority")),
                "risk": _text(r.get("risk")),
                "impact": _text(r.get("impact")),
                "guardrail": _text(r.get("guardrail")),
            }
            for r in document.get("risks", [])
        ],
        "cost_timeline": _text(document.get("cost_timeline")),
        "cost_paragraphs": list(document.get("cost_timeline", {}).get("paragraphs", [])),
        "detail_refs": [
            {
                "ref": _text(r.get("ref")),
                "locator": _text(r.get("locator")),
                "label": _text(r.get("label")),
            }
            for r in document.get("detail_refs", [])
        ],
        "evidence_refs": [
            {
                "ref": _text(r.get("ref")),
                "locator": _text(r.get("locator")),
                "description": _text(r.get("description")),
            }
            for r in document.get("evidence_refs", [])
        ],
        "terminal_bullets": list(document.get("terminal_outcome", {}).get("bullets", [])),
        "incompleteness_guardrails": list(
            document.get("terminal_outcome", {}).get("incompleteness_guardrails", [])
        ),
        "must_still_work": list(document.get("must_still_work", [])),
        "pm_view": [
            {
                "anchor": _text(p.get("anchor")),
                "quote": _text(p.get("quote")),
            }
            for p in document.get("pm_view", [])
        ],
        "pm_anchor_map": [
            {
                "technical_sections": list(p.get("technical_sections", [])),
                "controlling_sentence": _text(p.get("controlling_sentence")),
                "how_it_serves": _text(p.get("how_it_serves")),
            }
            for p in document.get("pm_anchor_map", [])
        ],
        "terminal_outcome": _text(document.get("terminal_outcome", {}).get("statement")),
        "sections": sections,
        "section_ids": all_section_ids,
        # Gate + authority fields (SC-14 two-gate model).
        "gate": {
            "gate_kind": document.get("gate_kind", ""),
            "gate_mode": document.get("gate_mode", ""),
            "intent": document.get("intent", ""),
            "repair_authority": document.get("repair_authority", ""),
            # Preserve the contract's literal vocabulary in projections. The
            # server substitutes <reported-sha256> only for an exact-digest
            # decision receipt, never for regenerated review Markdown.
            "required_approval_vocabulary": _text(document.get("required_approval_vocabulary")),
            "approval_vocabulary_derivation": document.get("approval_vocabulary_derivation") or {},
            "first_repair_authorizing": document.get("first_repair_authorizing"),
            "revision_policy": document.get("revision_policy", {}),
            "fix_settings_path": document.get("fix_settings_path", ""),
            "fix_settings_sha256": document.get("fix_settings_sha256", ""),
        },
        "parent_contracts": [
            {
                "path": p.get("path", ""),
                "sha256": p.get("sha256", ""),
                "relation": p.get("relation", ""),
            }
            for p in document.get("parent_contracts", [])
        ],
    }

    # Defect-contract-only authority surfaces.
    if is_defect:
        view_model["accepted_user_outcome"] = document.get("accepted_user_outcome", {})
        view_model["conviction_state"] = document.get("conviction_state", {})
        view_model["out_of_scope_boundaries"] = [
            {
                "boundary": _text(b.get("boundary")),
                "reason": _text(b.get("reason")),
            }
            for b in document.get("out_of_scope_boundaries", [])
        ]
        view_model["probe_constraints"] = [
            _text(c.get("constraint")) for c in document.get("probe_constraints", [])
        ]

    return view_model

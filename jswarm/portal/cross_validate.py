"""Cross-document validation for COM-389 fix decisions.

Structural JSON Schema cannot express cross-document referential constraints,
so they are enforced here:

1. Digest chain: the fix contract's live bytes must hash to the publication
   manifest's contract_sha256.
2. Parent binding: the fix contract's parent_defect_contract must match the
   parent defect contract document and its recorded digest.
3. Scope trace: every authorized_defects[].parent_scope_trace.defect_contract
   must equal the parent path; defect_key must exist in the parent's defects[]
   and the parent's disposition for it must be in-scope.
4. PM anchoring: pm_anchor_map[].technical_sections entries must resolve to
   section ids present in the contract (native documents only; legacy-migration
   conversions are exempt).
"""

import hashlib
import json
from pathlib import Path


def _sha256_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def section_ids(contract):
    """All section ids in a contract, including nested subsections."""
    ids = set()

    def walk(entries):
        for entry in entries:
            ids.add(entry["section_id"])
            walk(entry.get("subsections", []))

    walk(contract.get("sections", []))
    return ids


def validate_pm_anchor_refs(contract):
    """Return a list of PM-anchor reference errors (empty when valid).

    Exempt when provenance.conversion == 'legacy-migration' or PM anchoring is
    not required (pm_anchor_map empty is a schema-level concern; here we only
    check dangling references).
    """
    errors = []
    known = section_ids(contract)
    for entry in contract.get("pm_anchor_map", []):
        for ref in entry.get("technical_sections", []):
            if ref not in known:
                errors.append(
                    f"pm_anchor_map technical_sections references unknown section_id {ref!r}"
                )
    return errors


def cross_validate(fix_contract_path, parent_defect_path, manifest_path):
    """Validate the (fix, parent defect, manifest) triple.

    Returns (ok, errors) where errors is a list of human-readable strings.
    """
    errors = []
    fix_path = Path(fix_contract_path)
    parent_path = Path(parent_defect_path)
    manifest_path = Path(manifest_path)

    fix = json.loads(fix_path.read_text(encoding="utf-8"))
    parent = json.loads(parent_path.read_text(encoding="utf-8"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    # 1. Digest chain: live fix bytes vs manifest digest.
    live = _sha256_file(fix_path)
    if live != manifest["contract_sha256"]:
        errors.append(
            f"fix contract digest {live} does not match manifest contract_sha256 "
            f"{manifest['contract_sha256']}"
        )

    # 2. Parent binding: path and digest must match the parent document.
    binding = fix.get("parent_defect_contract", {})
    if binding.get("path") != parent.get("source_path"):
        errors.append(
            f"parent_defect_contract.path {binding.get('path')!r} != parent source_path {parent.get('source_path')!r}"
        )
    parent_live = _sha256_file(parent_path)
    if binding.get("sha256") != parent_live:
        errors.append(
            f"parent_defect_contract.sha256 {binding.get('sha256')} != computed parent digest {parent_live}"
        )

    # 3. Scope traces: every authorized leg must resolve in the parent.
    parent_defects = {d["key"]: d for d in parent.get("defects", [])}
    for leg in fix.get("authorized_defects", []):
        trace = leg.get("parent_scope_trace", {})
        key = trace.get("defect_key")
        if trace.get("defect_contract") != binding.get("path"):
            errors.append(
                f"authorized defect {leg.get('key')!r} trace defect_contract "
                f"{trace.get('defect_contract')!r} != parent_defect_contract.path {binding.get('path')!r}"
            )
        if key not in parent_defects:
            errors.append(
                f"authorized defect {leg.get('key')!r}: defect_key {key!r} not found in parent defects"
            )
            continue
        psa = parent_defects[key].get("preliminary_scope_analysis", {})
        if psa.get("disposition") != "in-scope":
            errors.append(
                f"authorized defect {leg.get('key')!r}: parent disposition for {key!r} is "
                f"{psa.get('disposition')!r}, not in-scope — no repair authority"
            )

    # 4. PM anchor references (native only).
    errors.extend(validate_pm_anchor_refs(fix))

    return (not errors), errors

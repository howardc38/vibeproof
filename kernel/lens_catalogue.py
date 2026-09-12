"""Resolve shared question text and legacy lens views from one current corpus."""
import json
from pathlib import Path

CATALOGUE = ".v4/lens_catalogue.json"
CONTRACT = ".v4/review_contract.json"


def metadata(root):
    path = Path(root) / CATALOGUE
    if not path.exists():
        return {"schema": 1, "entries": {}, "legacy": {}}
    data = json.loads(path.read_text())
    if data.get("schema") != 1 or not isinstance(data.get("entries"), dict) or not isinstance(data.get("legacy"), dict):
        raise ValueError("invalid lens catalogue")
    return data


def coordination(root):
    path = Path(root) / CONTRACT
    if not path.exists():
        return []
    data = json.loads(path.read_text())
    if data.get("schema") != 1 or not isinstance(data.get("checks"), list):
        raise ValueError("invalid review contract")
    return data["checks"]


def resolve_checks(checks, indexed=None):
    """Expand question references while keeping each source identity and metadata."""
    if indexed is None:
        indexed = {c["id"]: c for c in checks if isinstance(c, dict) and c.get("id")}
    def expand(check, visiting=()):
        if not isinstance(check, dict) or not check.get("same_as"):
            return check
        target = check["same_as"]
        if target in visiting or target not in indexed:
            raise ValueError("cyclic or missing question reference: " + target)
        resolved = expand(indexed[target], (*visiting, target))
        text = resolved.get("check") or resolved.get("text")
        if not isinstance(text, str) or not text.strip():
            raise ValueError("shared question has no text: " + target)
        return {**check, "check": text}

    return [expand(c) for c in checks]


def resolve(root, lenses, *, include_legacy=False):
    data = metadata(root)
    indexed, owners = {}, {}
    for owner, checks in [(k, v["checks"]) for k, v in lenses.items()] + [("review-contract", coordination(root))]:
        for check in checks:
            if not isinstance(check, dict) or not check.get("id"):
                continue
            cid = check["id"]
            if cid in indexed:
                raise ValueError("duplicate check identity: " + cid)
            indexed[cid], owners[cid] = check, owner
    for cid, entry in data["entries"].items():
        if cid not in indexed or owners[cid] != entry["owner"]:
            raise ValueError("catalogue responsibility has no matching owner: " + cid)


    out = {slug: {**lens, "checks": resolve_checks(lens["checks"], indexed)} for slug, lens in lenses.items()}
    if include_legacy:
        for slug, view in data["legacy"].items():
            if slug in out:
                continue  # A preserved adopter-authored lens takes precedence over a generated view.
            out[slug] = {**view, "legacy_view": True,
                         "checks": resolve_checks([indexed[cid] for cid in view["check_ids"]], indexed)}
    return out


def identity_lens(root, slug, check_id=None):
    """A moved check keeps its original claim namespace; old rows never change."""
    if not check_id:
        return slug
    entry = metadata(root)["entries"].get(check_id)
    if entry is None:
        return slug  # Newly authored checks have no historical namespace to preserve.
    if slug not in (entry["owner"], entry["legacy_lens"]):
        raise ValueError("check does not belong to the requested lens")
    return entry["legacy_lens"]

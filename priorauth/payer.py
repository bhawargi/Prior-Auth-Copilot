"""Mock payer: auth-requirement lookup, submission intake and utilization review."""
import itertools
import re

from . import data

_ids = itertools.count(1)


def auth_requirement(payer_id: str, cpt: str) -> dict:
    payer = data.get_payer(payer_id)
    policy_id = payer["auth_required"].get(cpt)
    return {
        "payer": payer["name"],
        "cpt": cpt,
        "auth_required": policy_id is not None,
        "policy_id": policy_id,
    }


def new_submission_id(kind: str) -> str:
    return f"{'APL' if kind == 'appeal' else 'PA'}-{2026_11_000 + next(_ids)}"


def _denial(criterion: dict) -> dict:
    return {
        "decision": "denied",
        "denial_code": f"DOC-INSUFF-{criterion['id']}",
        "criterion_id": criterion["id"],
        "reason": criterion["denial_reason"],
    }


def review(policy_id: str, packet: dict, force_denial: bool = False) -> dict:
    """Decide a submission. Every policy criterion needs status 'met' and a quote
    that satisfies the payer's (hidden) documentation rule.

    force_denial (demo control) denies an initial request on its most thinly
    evidenced criterion, so the appeal loop can be shown with any model."""
    policy = data.get_policy(policy_id)
    by_id = {c["criterion_id"]: c for c in packet["criteria"]}
    if force_denial and packet["kind"] == "initial":
        weakest = min(policy["criteria"], key=lambda c: len((by_id.get(c["id"]) or {}).get("evidence", [])))
        return _denial(weakest)
    for criterion in policy["criteria"]:
        entry = by_id.get(criterion["id"])
        quotes = [e["quote"] for e in (entry or {}).get("evidence", [])]
        ok = (
            entry is not None
            and entry.get("status") == "met"
            and any(re.search(criterion["payer_rule"], q, re.I) for q in quotes)
        )
        if not ok:
            return _denial(criterion)
    return {"decision": "approved", "auth_number": f"AUTH-{next(_ids):06d}", "valid_days": 60}

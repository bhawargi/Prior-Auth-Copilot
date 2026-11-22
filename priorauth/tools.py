"""Tools the agent can call. Each handler returns (result, pause_status).

Guardrails live here, in code, not in the prompt: evidence quotes must exist
verbatim in the chart, and nothing reaches the payer without clinician approval.
"""
import re

from . import data, payer

STOPWORDS = {"the", "and", "for", "with", "of", "to", "in", "on", "a", "an", "or", "is", "was", "at", "by", "any", "documented"}
FINISH_OUTCOMES = ("approved", "not_required", "denied_final", "needs_human")


def _err(message, **extra):
    return {"ok": False, "error": message, **extra}, None


def _latest_submission(case):
    return case["submissions"][-1] if case["submissions"] else None


def check_auth_required(case, args):
    cpt = str(args.get("cpt_code") or case["order"]["cpt"])
    result = payer.auth_requirement(case["payer_id"], cpt)
    case["auth"] = result
    if result["auth_required"]:
        case["policy_id"] = result["policy_id"]
    return result, None


def get_payer_policy(case, args):
    policy_id = args.get("policy_id") or case.get("policy_id")
    policy = data.get_policy(policy_id) if policy_id else None
    if not policy:
        return _err(f"Unknown policy_id {policy_id!r}. Call check_auth_required first.")
    case["policy_id"] = policy_id
    return {
        "policy_id": policy_id,
        "title": policy["title"],
        "summary": policy["summary"],
        "criteria": [{"criterion_id": c["id"], "text": c["text"]} for c in policy["criteria"]],
    }, None


def list_chart_documents(case, args):
    docs = data.get_patient(case["patient_id"])["documents"]
    return {"documents": [{k: d[k] for k in ("doc_id", "date", "type", "title")} for d in docs]}, None


def read_chart_document(case, args):
    doc = data.get_document(case["patient_id"], args.get("doc_id", ""))
    if not doc:
        return _err(f"No document {args.get('doc_id')!r}. Use list_chart_documents.")
    return dict(doc), None


def search_chart(case, args):
    query = str(args.get("query", ""))
    terms = [t for t in re.findall(r"[a-z0-9]+", query.lower()) if len(t) > 2 and t not in STOPWORDS]
    hits = []
    for doc in data.get_patient(case["patient_id"])["documents"]:
        for sentence in data.sentences(doc["text"]):
            low = sentence.lower()
            score = sum(t in low for t in terms) + (3 if query.lower() in low else 0)
            if score:
                hits.append((score, {"doc_id": doc["doc_id"], "date": doc["date"], "title": doc["title"], "sentence": sentence}))
    hits.sort(key=lambda h: -h[0])
    return {"query": query, "matches": [h[1] for h in hits[:6]]}, None


def prepare_request(case, args):
    kind = args.get("kind", "initial")
    policy = data.get_policy(case.get("policy_id") or "")
    if not policy:
        return _err("Load the payer policy with get_payer_policy before preparing a request.")
    last = _latest_submission(case)
    if kind == "appeal" and not (last and last["status"] == "denied"):
        return _err("An appeal needs a denied submission. Use kind='initial'.")
    if kind == "initial" and last and last["status"] == "denied":
        return _err("The last submission was denied. Prepare an appeal (kind='appeal').")

    errors = []
    criteria = args.get("criteria") or []
    by_id = {c.get("criterion_id"): c for c in criteria if isinstance(c, dict)}
    for c in policy["criteria"]:
        if c["id"] not in by_id:
            errors.append(f"Missing criterion {c['id']}.")
    clean = []
    for entry in by_id.values():
        status = entry.get("status")
        if status not in ("met", "not_met", "unclear"):
            errors.append(f"{entry.get('criterion_id')}: status must be met, not_met or unclear.")
        evidence = []
        for ev in entry.get("evidence") or []:
            doc = data.get_document(case["patient_id"], ev.get("doc_id", ""))
            quote = str(ev.get("quote", "")).strip()
            if not doc:
                errors.append(f"{entry.get('criterion_id')}: unknown doc_id {ev.get('doc_id')!r}.")
            elif not quote or data.normalize(quote).rstrip(".") not in data.normalize(doc["text"]):
                errors.append(f"{entry.get('criterion_id')}: quote not found verbatim in {doc['doc_id']}: {quote[:80]!r}")
            else:
                evidence.append({"doc_id": doc["doc_id"], "date": doc["date"], "title": doc["title"], "quote": quote})
        if status == "met" and not evidence:
            errors.append(f"{entry.get('criterion_id')}: status 'met' needs at least one evidence quote.")
        criterion_text = next((c["text"] for c in policy["criteria"] if c["id"] == entry.get("criterion_id")), "")
        clean.append({"criterion_id": entry.get("criterion_id"), "criterion": criterion_text,
                      "status": status, "evidence": evidence, "note": entry.get("note", "")})
    letter = str(args.get("letter", "")).strip()
    if len(letter) < 40:
        errors.append("Include a letter of medical necessity (or appeal letter) of at least a few sentences.")
    if errors:
        return {"ok": False, "errors": errors, "hint": "Fix these and call prepare_request again."}, None

    version = (case["packet"] or {}).get("version", 0) + 1
    case["packet"] = {"kind": kind, "version": version, "criteria": clean, "letter": letter,
                      "gaps": [c["criterion_id"] for c in clean if c["status"] != "met"],
                      "approved": False, "submitted": False}
    return {"ok": True, "packet_version": version,
            "message": "Packet saved and sent to the clinician for review. Stop here; you will be told when they respond."}, "awaiting_review"


def submit_to_payer(case, args):
    packet = case.get("packet")
    if not packet:
        return _err("No packet prepared. Call prepare_request first.")
    if not packet["approved"]:
        return _err("Blocked: the clinician has not approved this packet. Nothing can be submitted without sign-off.")
    if packet["submitted"]:
        return _err("This packet was already submitted. Use wait_for_payer or check_payer_status.")
    submission = {"id": payer.new_submission_id(packet["kind"]), "kind": packet["kind"],
                  "packet_version": packet["version"], "status": "pending", "result": None}
    case["submissions"].append(submission)
    packet["submitted"] = True
    return {"ok": True, "submission_id": submission["id"], "status": "pending",
            "message": "Received by payer. Decisions usually take 1-3 business days. Call wait_for_payer."}, None


def wait_for_payer(case, args):
    last = _latest_submission(case)
    if not (last and last["status"] == "pending"):
        return _err("No pending submission to wait for.")
    return {"ok": True, "message": "Case parked until the payer responds."}, "waiting_on_payer"


def check_payer_status(case, args):
    last = _latest_submission(case)
    if not last:
        return _err("Nothing has been submitted yet.")
    return {"submission_id": last["id"], "kind": last["kind"], "status": last["status"], **(last["result"] or {})}, None


def finish_case(case, args):
    outcome = args.get("outcome")
    if outcome not in FINISH_OUTCOMES:
        return _err(f"outcome must be one of {FINISH_OUTCOMES}.")
    last = _latest_submission(case)
    if outcome == "approved" and not (last and last["status"] == "approved"):
        return _err("Cannot close as approved: the payer has not approved a submission.")
    if outcome == "not_required" and (case.get("auth") or {}).get("auth_required", True):
        return _err("Cannot close as not_required: authorization is required or was not checked.")
    case["outcome"] = {"outcome": outcome, "summary": str(args.get("summary", ""))}
    return {"ok": True, "closed": outcome}, "closed"


def _fn(name, description, properties=None, required=None):
    return {"type": "function", "function": {"name": name, "description": description, "parameters": {
        "type": "object", "properties": properties or {}, "required": required or []}}}


_EVIDENCE = {"type": "array", "items": {"type": "object", "properties": {
    "doc_id": {"type": "string"},
    "quote": {"type": "string", "description": "Exact sentence copied verbatim from the document."}},
    "required": ["doc_id", "quote"]}}

SCHEMAS = [
    _fn("check_auth_required", "Check whether the patient's payer requires prior authorization for the ordered procedure, and which policy applies.",
        {"cpt_code": {"type": "string"}}, ["cpt_code"]),
    _fn("get_payer_policy", "Get the payer's medical-necessity policy and its criteria.",
        {"policy_id": {"type": "string"}}, ["policy_id"]),
    _fn("list_chart_documents", "List the documents in the patient's chart (EHR)."),
    _fn("read_chart_document", "Read the full text of one chart document.",
        {"doc_id": {"type": "string"}}, ["doc_id"]),
    _fn("search_chart", "Search the patient's chart for sentences matching a query. Returns matching sentences with doc_id.",
        {"query": {"type": "string"}}, ["query"]),
    _fn("prepare_request", "Save a prior-auth (or appeal) packet and send it to the clinician for review. Every policy criterion must be listed. Quotes must be copied verbatim from chart documents.",
        {"kind": {"type": "string", "enum": ["initial", "appeal"]},
         "criteria": {"type": "array", "items": {"type": "object", "properties": {
             "criterion_id": {"type": "string"},
             "status": {"type": "string", "enum": ["met", "not_met", "unclear"]},
             "evidence": _EVIDENCE,
             "note": {"type": "string", "description": "Optional note for the clinician, e.g. what is missing."}},
             "required": ["criterion_id", "status", "evidence"]}},
         "letter": {"type": "string", "description": "Letter of medical necessity, or appeal letter answering the denial reason, citing the evidence."}},
        ["kind", "criteria", "letter"]),
    _fn("submit_to_payer", "Submit the clinician-approved packet to the payer portal."),
    _fn("wait_for_payer", "Park the case until the payer responds. Use right after submitting."),
    _fn("check_payer_status", "Get the status and decision for the latest submission."),
    _fn("finish_case", "Close the case.",
        {"outcome": {"type": "string", "enum": list(FINISH_OUTCOMES)},
         "summary": {"type": "string", "description": "One or two sentences for the care team."}},
        ["outcome", "summary"]),
]

HANDLERS = {
    "check_auth_required": check_auth_required,
    "get_payer_policy": get_payer_policy,
    "list_chart_documents": list_chart_documents,
    "read_chart_document": read_chart_document,
    "search_chart": search_chart,
    "prepare_request": prepare_request,
    "submit_to_payer": submit_to_payer,
    "wait_for_payer": wait_for_payer,
    "check_payer_status": check_payer_status,
    "finish_case": finish_case,
}


def run_tool(case, name, args):
    handler = HANDLERS.get(name)
    if not handler:
        return _err(f"Unknown tool {name!r}.")
    if not isinstance(args, dict) or "_invalid_json" in args:
        return _err("Arguments must be a valid JSON object matching the tool schema.")
    return handler(case, args)

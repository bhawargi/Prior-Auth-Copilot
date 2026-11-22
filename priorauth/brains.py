import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

from . import config, data
from .tools import SCHEMAS

SYSTEM_PROMPT = """You are Prior-Auth Copilot, an AI agent that handles prior authorization for a clinic's staff. You work one case at a time by calling tools.

Workflow:
1. Call check_auth_required for the ordered procedure. If authorization is not required, call finish_case with outcome "not_required".
2. Call get_payer_policy, then gather evidence from the chart (list_chart_documents, read_chart_document, search_chart).
3. For EVERY policy criterion decide met / not_met / unclear, citing sentences copied exactly, word for word, from chart documents.
4. Call prepare_request (kind "initial") with a concise letter of medical necessity. Then stop: a clinician reviews the packet.
5. When told the clinician approved, call submit_to_payer, then wait_for_payer. If the clinician returned the packet with a note, address the note and call prepare_request again.
6. When told the payer responded, call check_payer_status. If approved, call finish_case with outcome "approved". If denied, read the denial reason, search the chart for evidence that answers it, and call prepare_request with kind "appeal" and an appeal letter that responds to the reason. If an appeal is denied, call finish_case with outcome "needs_human".

Rules:
- Never invent clinical facts. If evidence is missing, mark the criterion "unclear" and say what is needed in its note.
- If a tool returns errors, fix them and try again.
- Before each tool call, briefly say what you are doing and why in one sentence.
- Always continue by calling a tool."""


def make_brain():
    return WatsonxBrain() if config.AGENT_MODE == "watsonx" else MockBrain()


def _call(name, args=None):
    return {"id": f"call_{uuid.uuid4().hex[:10]}", "name": name, "args": args or {}}


# --------------------------------------------------------------------------- watsonx

class WatsonxBrain:
    label = "IBM watsonx.ai"

    def __init__(self):
        self.model = config.WATSONX_MODEL_ID
        self._token = None
        self._token_expiry = 0

    def _iam_token(self):
        if self._token and time.time() < self._token_expiry - 60:
            return self._token
        body = urllib.parse.urlencode({
            "grant_type": "urn:ibm:params:oauth:grant-type:apikey",
            "apikey": config.WATSONX_APIKEY,
        }).encode()
        req = urllib.request.Request(config.IAM_URL, data=body, headers={
            "Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"})
        payload = _http_json(req)
        self._token = payload["access_token"]
        self._token_expiry = payload.get("expiration", time.time() + 3000)
        return self._token

    def next_turn(self, case):
        body = {
            "model_id": self.model,
            "project_id": config.WATSONX_PROJECT_ID,
            "messages": [{"role": "system", "content": SYSTEM_PROMPT}] + case["messages"],
            "tools": SCHEMAS,
            "tool_choice_option": "auto",
            "max_completion_tokens": 2048,
            "max_tokens": 2048,
            "temperature": 0,
        }
        url = f"{config.WATSONX_URL}/ml/v1/text/chat?version={config.WATSONX_API_VERSION}"
        req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={
            "Authorization": f"Bearer {self._iam_token()}",
            "Content-Type": "application/json", "Accept": "application/json"})
        message = _http_json(req)["choices"][0]["message"]
        content = (message.get("content") or "").strip()
        calls = [self._parse_call(tc) for tc in message.get("tool_calls") or []]
        if calls or not content:
            return {"thought": content, "calls": calls}
        thought, calls, failed = _calls_from_text(content)
        if failed:
            # Keep the raw text in the conversation so the model sees what it sent.
            return {"thought": thought, "calls": [], "raw": content, "nudge": TEXT_CALL_HINT}
        return {"thought": thought, "calls": calls}

    @staticmethod
    def _parse_call(tc):
        fn = tc.get("function", {})
        raw = fn.get("arguments") or "{}"
        try:
            args = json.loads(raw) if isinstance(raw, str) else raw
            if isinstance(args, str):  # Granite sometimes double-encodes the arguments
                args = json.loads(args) if args.strip() else {}
        except json.JSONDecodeError:
            args = {"_invalid_json": raw}
        return {"id": tc.get("id") or f"call_{uuid.uuid4().hex[:10]}", "name": fn.get("name", ""), "args": args}


TEXT_CALL_HINT = ("Your last tool call was written as text and was not valid JSON, so it was not run. "
                  "Call the tool again through the function-calling interface, with complete, valid JSON arguments.")


def _calls_from_text(content):
    """Granite sometimes emits tool calls as text (<tool_call>{...}), especially for
    large arguments, and occasionally drops a closing bracket. Recover them.
    Returns (thought, calls, parse_failed)."""
    match = re.search(r"<tool_call>\s*(\[.*\]|\{.*)", content, re.S)
    if not match:
        return content, [], False
    thought = content[:match.start()].strip()
    body = re.sub(r"\s*</tool_call>\s*$", "", match.group(1).strip())
    parsed = None
    for candidate in (body, _close_brackets(body)):
        try:
            parsed = json.loads(candidate, strict=False)
            break
        except json.JSONDecodeError:
            continue
    if parsed is None:
        return thought, [], True
    items = parsed if isinstance(parsed, list) else [parsed]
    calls = []
    for item in items:
        if not isinstance(item, dict):
            continue
        args = item.get("arguments") or {}
        if isinstance(args, str):
            try:
                args = json.loads(args, strict=False)
            except json.JSONDecodeError:
                args = {"_invalid_json": args}
        calls.append(_call(item.get("name", ""), args))
    return thought, calls, not calls


def _close_brackets(text):
    """Append whatever closing brackets/braces an otherwise-complete JSON text is missing."""
    stack, in_string, escaped = [], False, False
    for ch in text:
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
        elif ch == '"':
            in_string = True
        elif ch in "{[":
            stack.append("}" if ch == "{" else "]")
        elif ch in "}]" and stack and stack[-1] == ch:
            stack.pop()
    return text + "".join(reversed(stack))


def _http_json(req):
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:600]
        raise RuntimeError(f"watsonx HTTP {e.code}: {detail}") from None


# --------------------------------------------------------------------------- mock

class MockBrain:

    label = "Mock agent (offline)"
    model = "scripted"

    def __init__(self, step_delay=None):
        # Pause between steps so the live timeline is watchable in a demo.
        self.step_delay = config.MOCK_STEP_DELAY if step_delay is None else step_delay

    def next_turn(self, case):
        time.sleep(self.step_delay)
        log = case["tool_log"]
        names = [entry["name"] for entry in log]
        since_event = names[case.get("event_at", 0):]
        order, patient = case["order"], data.get_patient(case["patient_id"])

        if "check_auth_required" not in names:
            return {"thought": f"New order: {order['description']} (CPT {order['cpt']}). First I'll check whether {data.get_payer(case['payer_id'])['name']} requires prior authorization.",
                    "calls": [_call("check_auth_required", {"cpt_code": order["cpt"]})]}
        if not case["auth"]["auth_required"]:
            return {"thought": "No authorization is needed for this code, so I can close the case and let scheduling proceed.",
                    "calls": [_call("finish_case", {"outcome": "not_required", "summary": f"{case['auth']['payer']} does not require prior authorization for CPT {order['cpt']}. Cleared to schedule."})]}
        if "get_payer_policy" not in names:
            return {"thought": f"Authorization is required under policy {case['policy_id']}. Loading the payer's criteria.",
                    "calls": [_call("get_payer_policy", {"policy_id": case["policy_id"]})]}
        if "list_chart_documents" not in names:
            return {"thought": "Now I'll see what's in the chart.", "calls": [_call("list_chart_documents")]}
        read = {e["args"].get("doc_id") for e in log if e["name"] == "read_chart_document"}
        unread = [d for d in patient["documents"] if d["doc_id"] not in read]
        if unread:
            d = unread[0]
            return {"thought": f"Reading {d['doc_id']} ({d['title']}, {d['date']}).",
                    "calls": [_call("read_chart_document", {"doc_id": d["doc_id"]})]}

        packet, subs = case["packet"], case["submissions"]
        last = subs[-1] if subs else None
        event = case.get("last_event")

        if event == "returned" and "prepare_request" not in since_event:
            note = case.get("clinician_note") or "no note"
            return {"thought": f"The clinician sent the packet back (\"{note}\"). I'll re-check the evidence and resubmit it for review.",
                    "calls": [self._prepare(case, packet["kind"])]}
        if packet is None:
            return {"thought": "I have evidence for each criterion. Drafting the request and letter of medical necessity for clinician sign-off.",
                    "calls": [self._prepare(case, "initial")]}
        if packet["approved"] and not packet["submitted"]:
            return {"thought": "Clinician approved. Submitting to the payer portal.", "calls": [_call("submit_to_payer")]}
        if names[-1] == "submit_to_payer" and last and last["status"] == "pending":
            return {"thought": "Submitted. Parking the case until the payer decides.", "calls": [_call("wait_for_payer")]}
        if event == "payer_update" and "check_payer_status" not in since_event:
            return {"thought": "The payer posted an update. Checking the decision.", "calls": [_call("check_payer_status")]}
        if last and last["status"] == "approved":
            r = last["result"]
            return {"thought": "Approved. Closing the case and notifying the care team.",
                    "calls": [_call("finish_case", {"outcome": "approved", "summary": f"Approved on {'appeal' if last['kind'] == 'appeal' else 'first submission'}. Authorization {r['auth_number']}, valid {r['valid_days']} days."})]}
        if last and last["status"] == "denied":
            if last["kind"] == "appeal":
                return {"thought": "The appeal was also denied. This needs a peer-to-peer review with a human.",
                        "calls": [_call("finish_case", {"outcome": "needs_human", "summary": f"Appeal denied: {last['result']['reason']} Recommend peer-to-peer review."})]}
            criterion = self._criterion(case, last["result"]["criterion_id"])
            if "search_chart" not in since_event:
                query = " ".join(criterion.get("mock_appeal_keywords") or criterion["mock_keywords"])
                return {"thought": f"Denied: \"{last['result']['reason']}\" The payer wants more specific documentation for {criterion['id']}. Searching the chart for evidence that answers this.",
                        "calls": [_call("search_chart", {"query": query})]}
            return {"thought": f"Found stronger evidence for {criterion['id']}. Drafting an appeal that responds directly to the denial reason.",
                    "calls": [self._prepare(case, "appeal", denied_id=criterion["id"])]}
        return {"thought": "Nothing left I can do automatically; handing to a human.",
                "calls": [_call("finish_case", {"outcome": "needs_human", "summary": "Agent could not determine a next step."})]}

    @staticmethod
    def _criterion(case, criterion_id):
        return next(c for c in data.get_policy(case["policy_id"])["criteria"] if c["id"] == criterion_id)

    @staticmethod
    def _find(patient, keywords, skip=()):
        for doc in sorted(patient["documents"], key=lambda d: d["date"]):
            for sentence in data.sentences(doc["text"]):
                if sentence not in skip and any(k.lower() in sentence.lower() for k in keywords):
                    return {"doc_id": doc["doc_id"], "quote": sentence, "date": doc["date"]}
        return None

    def _prepare(self, case, kind, denied_id=None):
        patient = data.get_patient(case["patient_id"])
        policy = data.get_policy(case["policy_id"])
        previous = {c["criterion_id"]: c for c in (case["packet"] or {}).get("criteria", [])}
        criteria, cited = [], []
        for c in policy["criteria"]:
            evidence = [{"doc_id": e["doc_id"], "quote": e["quote"]} for e in previous.get(c["id"], {}).get("evidence", [])]
            if not evidence:
                hit = self._find(patient, c["mock_keywords"])
                evidence = [hit] if hit else []
            if c["id"] == denied_id:
                hit = self._find(patient, c.get("mock_appeal_keywords") or c["mock_keywords"], skip={e["quote"] for e in evidence})
                if hit:
                    evidence.append(hit)
            evidence = [{"doc_id": e["doc_id"], "quote": e["quote"]} for e in evidence]
            criteria.append({"criterion_id": c["id"], "status": "met" if evidence else "unclear", "evidence": evidence,
                             "note": "" if evidence else "No supporting documentation found in the chart."})
            cited += [(c, e) for e in evidence]
        return _call("prepare_request", {"kind": kind, "criteria": criteria, "letter": self._letter(case, patient, policy, cited, kind, denied_id)})

    @staticmethod
    def _letter(case, patient, policy, cited, kind, denied_id):
        order = patient["order"]
        lines = []
        if kind == "appeal":
            last = case["submissions"][-1]
            lines.append(f"Re: Appeal of denial {last['result']['denial_code']} (submission {last['id']}) for {order['description']} (CPT {order['cpt']})")
            lines.append(f"Patient: {patient['name']}, member ID {patient['member_id']}")
            lines.append("")
            lines.append(f"The denial states: \"{last['result']['reason']}\" The medical record documents this directly:")
            for c, e in cited:
                if c["id"] == denied_id:
                    lines.append(f"  - \"{e['quote']}\" ({e['doc_id']})")
            lines.append("")
            lines.append(f"All other criteria of {policy['title'].split(':')[0]} remain met as previously documented. I respectfully request that the denial be overturned.")
        else:
            lines.append(f"Re: Prior authorization request for {order['description']} (CPT {order['cpt']})")
            lines.append(f"Patient: {patient['name']}, member ID {patient['member_id']}. Diagnosis: {order['diagnosis']}")
            lines.append("")
            lines.append(f"I am requesting authorization for the above study. The patient meets the criteria of {policy['title'].split(':')[0]}:")
            for c, e in cited:
                lines.append(f"  - {c['id']} ({c['text']}): \"{e['quote']}\" ({e['doc_id']})")
            lines.append("")
            lines.append("The results will directly guide treatment decisions.")
        lines.append("")
        lines.append(f"Sincerely,\n{order['ordering_clinician']}")
        return "\n".join(lines)

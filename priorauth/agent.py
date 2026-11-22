"""Case store and the agent loop: plan (brain) -> act (tool) -> observe (result),
pausing whenever a human or the payer has to respond, and resuming on events."""
import copy
import json
import threading
import time
import traceback
import uuid

from . import config, data, payer, tools
from .brains import make_brain


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%S")


class CaseStore:
    def __init__(self, brain=None, background=True):
        self.brain = brain or make_brain()
        self.background = background
        self.cases = {}
        self.locks = {}
        config.CASES_DIR.mkdir(exist_ok=True)

    # ------------------------------------------------------------------ queries

    def list(self):
        return [self._summary(c) for c in sorted(self.cases.values(), key=lambda c: c["created_at"], reverse=True)]

    def get(self, case_id):
        case = self.cases.get(case_id)
        if case is None:
            return None
        for _ in range(5):  # the agent thread may be mid-update; retry the copy
            try:
                snap = copy.deepcopy(case)
                break
            except RuntimeError:
                time.sleep(0.01)
        snap.pop("messages", None)
        return snap

    @staticmethod
    def _summary(c):
        return {k: c[k] for k in ("id", "patient_name", "status", "created_at", "updated_at")} | {
            "order": c["order"]["description"], "payer": c["payer_name"]}

    # ------------------------------------------------------------------ commands

    def create(self, patient_id):
        patient = data.get_patient(patient_id)
        case = {
            "id": f"CASE-{uuid.uuid4().hex[:6].upper()}",
            "patient_id": patient_id,
            "patient_name": patient["name"],
            "payer_id": patient["payer_id"],
            "payer_name": data.get_payer(patient["payer_id"])["name"],
            "order": patient["order"],
            "status": "working",
            "created_at": _now(),
            "updated_at": _now(),
            "agent": {"label": self.brain.label, "model": self.brain.model},
            "auth": None, "policy_id": None, "packet": None, "submissions": [],
            "outcome": None, "error": None, "clinician_note": None,
            "last_event": None, "event_at": 0,
            "timeline": [], "tool_log": [], "messages": [],
        }
        self.cases[case["id"]] = case
        self.locks[case["id"]] = threading.Lock()
        o = patient["order"]
        self._event(case, "order", "New order received",
                    f"{o['description']} (CPT {o['cpt']}) for {patient['name']}, {case['payer_name']} member {patient['member_id']}. Ordered by {o['ordering_clinician']}.",
                    message=(f"New order. Patient {patient['name']} (id {patient_id}, member {patient['member_id']}), payer {case['payer_name']}. "
                             f"Procedure: {o['description']}, CPT {o['cpt']}. Diagnosis: {o['diagnosis']}. Ordering clinician: {o['ordering_clinician']}. "
                             "Work this prior authorization."))
        self._start(case)
        return case["id"]

    def review(self, case_id, decision, note=""):
        case = self.cases[case_id]
        if case["status"] != "awaiting_review":
            raise ValueError("This case is not waiting for clinician review.")
        note = (note or "").strip()
        if decision == "approve":
            case["packet"]["approved"] = True
            case["clinician_note"] = note or None
            self._event(case, "human", "Clinician approved the packet", note or None,
                        message=f"The clinician approved packet version {case['packet']['version']}." + (f" Note: {note}" if note else "") + " Submit it to the payer.",
                        kind_event="approved")
        elif decision == "return":
            if not note:
                raise ValueError("Add a note telling the agent what to change.")
            case["clinician_note"] = note
            self._event(case, "human", "Clinician sent the packet back", note,
                        message=f"The clinician returned the packet without approving it. Their note: \"{note}\". Address it and call prepare_request again.",
                        kind_event="returned")
        else:
            raise ValueError("decision must be 'approve' or 'return'.")
        self._start(case)

    def payer_respond(self, case_id, force_denial=False):
        case = self.cases[case_id]
        last = case["submissions"][-1] if case["submissions"] else None
        if case["status"] != "waiting_on_payer" or not last or last["status"] != "pending":
            raise ValueError("No submission is waiting on the payer.")
        result = payer.review(case["policy_id"], case["packet"], force_denial=force_denial)
        last["status"], last["result"] = result["decision"], result
        detail = (f"Authorization {result['auth_number']}, valid {result['valid_days']} days." if result["decision"] == "approved"
                  else f"{result['denial_code']}: {result['reason']}")
        self._event(case, "payer", f"{case['payer_name']} {result['decision']} {last['id']}", detail,
                    message=f"Two days later: the payer posted a decision on submission {last['id']}. Call check_payer_status.",
                    kind_event="payer_update")
        self._start(case)

    # ------------------------------------------------------------------ loop

    def _start(self, case):
        case["status"] = "working"
        self._save(case)
        if self.background:
            threading.Thread(target=self._run, args=(case,), daemon=True).start()
        else:
            self._run(case)

    def _run(self, case):
        with self.locks[case["id"]]:
            try:
                self._loop(case)
            except Exception as e:  # surface failures in the UI instead of hanging
                case["status"], case["error"] = "error", str(e)
                self._timeline(case, "error", "Agent stopped with an error", str(e))
                traceback.print_exc()
            self._save(case)

    def _loop(self, case):
        nudges = 0
        for _ in range(config.MAX_STEPS_PER_RUN):
            turn = self.brain.next_turn(case)
            thought, calls = turn.get("thought") or "", turn.get("calls") or []
            if thought:
                self._timeline(case, "thought", thought)
            assistant = {"role": "assistant"}
            if turn.get("raw") or thought:
                assistant["content"] = turn.get("raw") or thought
            if calls:
                assistant["tool_calls"] = [{"id": c["id"], "type": "function", "function": {
                    "name": c["name"], "arguments": json.dumps(c["args"])}} for c in calls]
            case["messages"].append(assistant)

            if not calls:
                nudges += 1
                if nudges > 3:
                    raise RuntimeError("The model stopped calling tools.")
                if turn.get("nudge"):
                    self._timeline(case, "error", "Malformed tool call; asking the model to retry")
                case["messages"].append({"role": "user", "content": turn.get("nudge") or "Continue by calling the next tool."})
                continue

            pause = None
            for call in calls:
                if pause:
                    result = {"ok": False, "error": "Skipped: the case is paused for an external response."}
                else:
                    result, pause = tools.run_tool(case, call["name"], call["args"])
                case["tool_log"].append({"name": call["name"], "args": call["args"], "result": result})
                case["messages"].append({"role": "tool", "tool_call_id": call["id"], "content": json.dumps(result)})
                self._timeline(case, "tool", *describe(call["name"], call["args"], result), data={"tool": call["name"], "args": call["args"], "result": result})
            self._save(case)
            if pause:
                case["status"] = pause
                if pause == "closed":
                    o = case["outcome"]
                    self._timeline(case, "done", f"Case closed: {o['outcome'].replace('_', ' ')}", o["summary"])
                return
        raise RuntimeError(f"Step limit ({config.MAX_STEPS_PER_RUN}) reached without the case pausing.")

    # ------------------------------------------------------------------ helpers

    def _timeline(self, case, kind, title, detail=None, data=None):
        case["timeline"].append({"ts": _now(), "kind": kind, "title": title, "detail": detail, "data": data})
        case["updated_at"] = _now()

    def _event(self, case, kind, title, detail, message, kind_event=None):
        self._timeline(case, kind, title, detail)
        case["messages"].append({"role": "user", "content": message})
        case["last_event"] = kind_event
        case["event_at"] = len(case["tool_log"])

    def _save(self, case):
        """Persist the full case, including the model conversation, as an audit log."""
        try:
            path = config.CASES_DIR / f"{case['id']}.json"
            path.write_text(json.dumps(case, indent=2, default=str))
        except (OSError, RuntimeError):
            pass


def describe(name, args, result):
    """Human-readable title and detail for a tool call in the timeline."""
    if isinstance(result, dict) and result.get("ok") is False:
        errs = result.get("errors") or [result.get("error", "")]
        return f"{name} rejected", "; ".join(errs)
    if name == "check_auth_required":
        return ("Checked payer rules: authorization required" if result["auth_required"] else "Checked payer rules: no authorization needed",
                f"{result['payer']}, CPT {result['cpt']}" + (f", policy {result['policy_id']}" if result["policy_id"] else ""))
    if name == "get_payer_policy":
        return f"Loaded policy {result['policy_id']}", f"{result['title']} ({len(result['criteria'])} criteria)"
    if name == "list_chart_documents":
        return f"Listed {len(result['documents'])} chart documents", ", ".join(f"{d['doc_id']} {d['title']}" for d in result["documents"])
    if name == "read_chart_document":
        return f"Read {result['doc_id']}: {result['title']}", f"{result['type']}, {result['date']}"
    if name == "search_chart":
        return f"Searched chart: \"{result['query']}\"", f"{len(result['matches'])} matching sentences"
    if name == "prepare_request":
        return f"Prepared packet v{result['packet_version']} and sent it for clinician review", None
    if name == "submit_to_payer":
        return f"Submitted {result['submission_id']} to payer portal", "Status: pending"
    if name == "wait_for_payer":
        return "Waiting for payer decision", "The agent resumes automatically when the payer responds."
    if name == "check_payer_status":
        extra = result.get("reason") or (f"Authorization {result.get('auth_number')}" if result.get("auth_number") else "")
        return f"Payer status for {result['submission_id']}: {result['status']}", extra or None
    if name == "finish_case":
        return "Closed the case", None
    return name, None

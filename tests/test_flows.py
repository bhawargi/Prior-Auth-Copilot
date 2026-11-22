"""End-to-end flows with the offline mock agent: python3 -m unittest discover tests"""
import unittest

from priorauth import tools
from priorauth.agent import CaseStore
from priorauth.brains import MockBrain


class FlowTests(unittest.TestCase):
    def setUp(self):
        self.store = CaseStore(brain=MockBrain(step_delay=0), background=False)

    def test_denial_then_appeal_is_approved(self):
        cid = self.store.create("P-1001")
        case = self.store.cases[cid]
        self.assertEqual(case["status"], "awaiting_review")
        self.assertEqual(case["packet"]["kind"], "initial")

        self.store.review(cid, "approve")
        self.assertEqual(case["status"], "waiting_on_payer")
        self.store.payer_respond(cid)
        self.assertEqual(case["submissions"][0]["status"], "denied")
        self.assertEqual(case["submissions"][0]["result"]["criterion_id"], "L2")
        self.assertEqual(case["status"], "awaiting_review")
        self.assertEqual(case["packet"]["kind"], "appeal")

        self.store.review(cid, "approve")
        self.store.payer_respond(cid)
        self.assertEqual(case["status"], "closed")
        self.assertEqual(case["outcome"]["outcome"], "approved")

    def test_clean_approval(self):
        cid = self.store.create("P-1002")
        self.store.review(self.store.cases[cid]["id"], "approve")
        self.store.payer_respond(cid)
        case = self.store.cases[cid]
        self.assertEqual(case["outcome"]["outcome"], "approved")
        self.assertEqual(len(case["submissions"]), 1)

    def test_not_required(self):
        cid = self.store.create("P-1003")
        case = self.store.cases[cid]
        self.assertEqual(case["status"], "closed")
        self.assertEqual(case["outcome"]["outcome"], "not_required")

    def test_return_with_note_regenerates_packet(self):
        cid = self.store.create("P-1002")
        self.store.review(cid, "return", "Please double-check the X-ray date.")
        case = self.store.cases[cid]
        self.assertEqual(case["status"], "awaiting_review")
        self.assertEqual(case["packet"]["version"], 2)

    def test_forced_denial_appeal_path(self):
        cid = self.store.create("P-1002")
        self.store.review(cid, "approve")
        self.store.payer_respond(cid, force_denial=True)
        self.store.review(cid, "approve")
        self.store.payer_respond(cid)
        self.assertEqual(self.store.cases[cid]["outcome"]["outcome"], "approved")


class GuardrailTests(unittest.TestCase):
    def setUp(self):
        self.store = CaseStore(brain=MockBrain(step_delay=0), background=False)
        self.case = self.store.cases[self.store.create("P-1002")]

    def test_submit_blocked_without_approval(self):
        result, _ = tools.run_tool(self.case, "submit_to_payer", {})
        self.assertFalse(result["ok"])
        self.assertIn("clinician has not approved", result["error"])

    def test_hallucinated_quote_rejected(self):
        args = {"kind": "initial", "letter": "x" * 50, "criteria": [
            {"criterion_id": cid, "status": "met", "evidence": [{"doc_id": "DOC-1", "quote": "Patient failed 12 weeks of PT."}]}
            for cid in ("K1", "K2", "K3")]}
        result, pause = tools.run_tool(self.case, "prepare_request", args)
        self.assertFalse(result["ok"])
        self.assertIsNone(pause)
        self.assertTrue(any("not found verbatim" in e for e in result["errors"]))

    def test_payer_rejects_follow_up_interval_as_symptom_duration(self):
        from priorauth import payer
        packet = {"kind": "initial", "criteria": [
            {"criterion_id": "L1", "status": "met", "evidence": [{"quote": "Return in 6 weeks."}]},
            {"criterion_id": "L2", "status": "met", "evidence": [{"quote": "Patient completed 8 weeks of physical therapy (16 sessions)."}]},
            {"criterion_id": "L3", "status": "met", "evidence": [{"quote": "No prior imaging of the lumbar spine."}]}]}
        self.assertEqual(payer.review("ACME-RAD-014", packet)["criterion_id"], "L1")
        packet["criteria"][0]["evidence"] = [{"quote": "Symptoms began after lifting boxes and have persisted for 3 months."}]
        self.assertEqual(payer.review("ACME-RAD-014", packet)["decision"], "approved")

    def test_cannot_close_approved_without_payer_approval(self):
        result, _ = tools.run_tool(self.case, "finish_case", {"outcome": "approved", "summary": "done"})
        self.assertFalse(result["ok"])


class TextToolCallTests(unittest.TestCase):
    def test_recovers_call_missing_closing_brace(self):
        from priorauth.brains import _calls_from_text
        text = 'Drafting.\n<tool_call> {"name": "prepare_request", "arguments": {"kind": "initial", "criteria": [{"criterion_id": "K1", "evidence": [{"doc_id": "DOC-1", "quote": "a {b} c"}]}]}\n</tool_call>'
        thought, calls, failed = _calls_from_text(text)
        self.assertFalse(failed)
        self.assertEqual(thought, "Drafting.")
        self.assertEqual(calls[0]["name"], "prepare_request")
        self.assertEqual(calls[0]["args"]["criteria"][0]["evidence"][0]["quote"], "a {b} c")

    def test_unrecoverable_call_is_flagged(self):
        from priorauth.brains import _calls_from_text
        _, calls, failed = _calls_from_text('<tool_call> {"name": "x", "arguments": {"a": 1,, }}')
        self.assertTrue(failed)
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()

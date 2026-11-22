"""HTTP server: JSON API plus the single-page UI. Run: python3 -m priorauth.server"""
import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import config, data
from .agent import CaseStore

STATIC = Path(__file__).resolve().parent / "static"
store = CaseStore()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        if "/api/cases/" not in self.path or self.command != "GET":  # skip polling noise
            super().log_message(fmt, *args)

    def _send(self, status, body, content_type="application/json"):
        raw = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def _body(self):
        length = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(length) or b"{}")

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            return self._send(200, (STATIC / "index.html").read_bytes(), "text/html; charset=utf-8")
        if self.path == "/api/meta":
            return self._send(200, {"mode": config.AGENT_MODE, "label": store.brain.label, "model": store.brain.model})
        if self.path == "/api/patients":
            return self._send(200, [{
                "id": p["id"], "name": p["name"], "payer": data.get_payer(p["payer_id"])["name"],
                "order": p["order"]["description"], "cpt": p["order"]["cpt"], "scenario": p["scenario"],
            } for p in data.list_patients()])
        if self.path == "/api/cases":
            return self._send(200, store.list())
        m = re.fullmatch(r"/api/cases/([\w-]+)", self.path)
        if m:
            case = store.get(m.group(1))
            return self._send(200, case) if case else self._send(404, {"error": "Not found"})
        self._send(404, {"error": "Not found"})

    def do_POST(self):
        try:
            body = self._body()
            if self.path == "/api/cases":
                return self._send(201, {"id": store.create(body["patient_id"])})
            m = re.fullmatch(r"/api/cases/([\w-]+)/(review|payer-respond)", self.path)
            if not m or m.group(1) not in store.cases:
                return self._send(404, {"error": "Not found"})
            if m.group(2) == "review":
                store.review(m.group(1), body.get("decision"), body.get("note", ""))
            else:
                store.payer_respond(m.group(1), force_denial=bool(body.get("force_denial")))
            self._send(200, {"ok": True})
        except (ValueError, KeyError) as e:
            self._send(400, {"error": str(e)})


def main():
    server = ThreadingHTTPServer(("127.0.0.1", config.PORT), Handler)
    print(f"Prior-Auth Copilot running at http://localhost:{config.PORT}  (agent: {store.brain.label}, model: {store.brain.model})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

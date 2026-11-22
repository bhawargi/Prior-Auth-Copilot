# How Prior-Auth Copilot was built with Bob

The prototype was built by Bhuvan Hawargi (Team Bobby) with **Bob** (IBM) as an AI pair programmer, working in Bob Code. Bhuvan chose the idea, made the product and platform decisions, provisioned IBM Cloud, and reviewed the work. Bob designed and wrote the code, debugged the watsonx setup, and tested everything end to end.

This is the full story behind the "How we built it" section of the [Technology Statement](technology-statement.md), with concrete examples.

## 1. From idea to design

- **Choosing the idea.**. Bhuvan brainstormed & shortlisted three ideas after looking at the challenge tracks: Prior-Auth Copilot, a matchday concierge, and a benefits navigator. Bob compared how agentic each one really is. Prior-auth won because it has a natural closed loop: the agent acts, the payer responds (approve or deny), and the agent must reason about the response and change its plan.
- **Design decisions.** Bob proposed a Python standard-library app with no dependencies, so it is easy to run and review. It uses a single agent loop with ten tools, and a "brain" interface with two implementations: IBM watsonx Granite, and an offline mock for demos without credentials.

## 2. Building the agent

- **Agent loop** (`priorauth/agent.py`). The model plans, calls a tool, reads the result and repeats. A tool can *pause* the case, either for clinician review or for the payer's decision. Events such as "clinician approved" or "payer responded" resume the same conversation, so the agent remembers the case across days.
- **Guardrails written as code** (`priorauth/tools.py`), so the model cannot talk its way past them:
  - `submit_to_payer` returns *"Blocked: the clinician has not approved this packet"* until a human approves.
  - `prepare_request` checks every evidence quote word for word against the cited chart document, and rejects the packet if any quote isn't there.
  - `finish_case` refuses to close a case as "approved" unless the payer actually approved it.
- **Synthetic data.** Bob wrote three synthetic patients with realistic chart notes, and two payers with medical-necessity policies. Each policy has a hidden review rule, for example "therapy duration must be stated", which the model never sees.
- **Web UI.** A single page shows the case and packet review, plus a live agent-activity feed. At Bhuvan's request, Bob moved the case view to the wide centre column.

## 3. Connecting to IBM watsonx: debugging the setup

Bob diagnosed each failure from IBM's own error messages and APIs, without guessing:

| Symptom | What Bob found | Fix |
|---|---|---|
| `.env` URL was `eu-gb.dataplatform.cloud.ibm.com` | That's the web interface, not the API | Switched to the `*.ml.cloud.ibm.com` API URL |
| IAM error *"Provided API key could not be found"* | A 154-character access token had been supplied instead of an IBM Cloud API key | Bhuvan created an API key |
| *"project … is not associated with a WML instance"* | No watsonx.ai Runtime attached to the project | Attach the Runtime to the project |
| Runtime wouldn't attach | Queried IBM's model catalogue: **no Granite chat model is offered in London**. Listed the account's resources: Studio and Runtime were all in **Dallas** | Moved to a Dallas project, attached in the watsonx portal rather than the Cloud Pak for Data portal |
| *"Failed to find project_id"* | `.env` still held the old London project ID. Listed projects via the API (the first attempt was blocked by Cloudflare, so Bob switched to `curl`) | Set the correct Dallas project ID |

## 4. Hardening against real Granite behaviour

Running live cases exposed problems that no mock would have shown:

- **Double-encoded arguments.** Granite returned tool arguments as a JSON string inside a JSON string, so every call would have been rejected. Bob added a decode step.
- **"Agent stopped calling tools" (James Carter case).** For the large `prepare_request` call, Granite sometimes wrote the tool call as plain text, with one closing brace missing. Bob read the saved audit log, confirmed the cause, and fixed it two ways:
  - a parser that closes unbalanced brackets and recovers the call;
  - if recovery fails, specific feedback to the model ("your tool call was not valid JSON; call it again properly") instead of a generic nudge.

  It then re-parsed Granite's actual broken output to prove the fix.
- **Self-correction, observed live.** Granite once cited *"No prior MRI of the lumbar spine documented in the chart"*, a sentence that isn't in the chart. The quote check rejected it, Granite searched again, and it cited the real sentence.

## 5. Making the demo honest

Granite cited *"Return in 6 weeks"* (a follow-up plan) as evidence that symptoms had lasted 6 weeks. The mock payer's rule was loose enough to accept it. Bob tightened the rule so only real duration statements count, such as "persisted for 3 months", and added a test. Now Granite's own mistake triggers a **genuine denial**. The agent then finds the right evidence and wins on appeal, with no forced denial needed.

## 6. Testing and verification

- **11 unit tests** (`tests/test_flows.py`):
  - end-to-end flows: denial then appeal, clean approval, not required, clinician sends the packet back, forced denial;
  - guardrails: no submission without approval, invented quotes rejected, no false closure;
  - the payer rule;
  - recovering malformed tool calls.
- **Live runs on watsonx Granite** for all three patients, driven both through the API and by clicking through the UI in a browser.
- **Statistics** in the Problem Statement checked against the AMA's 2025 Prior Authorization Physician Survey.

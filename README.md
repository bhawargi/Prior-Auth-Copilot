# Prior-Auth Copilot

**An AI agent that takes prior authorization from order to approval, including the appeal, with the clinician in control.**

> This repository, in its entirety, is a project for the **IBM SkillsBuild Program (September – November 2026)**, Smart Healthcare challenge.
> Team **Bobby** · Bhuvan Hawargi · Built on **IBM watsonx.ai** with **IBM Granite** · Synthetic data only.

## Why this project

Before many scans, procedures and drugs, a patient's insurer must approve them. This is called prior authorization. The AMA's 2025 physician survey found that physicians complete about **40 prior authorizations a week**, which takes **13 hours** of staff time. **95%** say it delays care, and **79%** have seen patients abandon treatment because of it.

The work is hard to automate with rules: payer policies are long free text, the evidence is buried in clinical notes, and every denial needs a tailored answer. That makes it a strong fit for **agentic AI**: a model that plans, uses tools, reacts to outcomes, and keeps going over days, with a human signing off on everything that matters.

## What it does

1. Checks whether the payer requires authorization for the order.
2. Loads the payer's policy and searches the chart for evidence for each criterion.
3. Drafts a packet with a **verbatim chart quote for every criterion**, plus a letter of medical necessity.
4. **Pauses for clinician review.** The clinician approves the packet or sends it back with a note.
5. Submits the packet and waits, even for days, for the payer's decision.
6. If denied, reads the reason, finds evidence that answers it, and drafts an **appeal**.

**Safeguards, enforced in code rather than in the prompt:**
- nothing is submitted without clinician approval;
- made-up quotes are rejected;
- a case can only close as "approved" if the payer approved it;
- every step is written to an audit log.

## Quick start

Needs Python 3.9+. There are no packages to install.

```bash
python3 -m priorauth.server
```

Open http://localhost:8000. Without credentials it runs an offline **mock agent**. To use **watsonx Granite**:

```bash
cp .env.example .env   # fill in WATSONX_APIKEY and WATSONX_PROJECT_ID
```

Run the tests:

```bash
python3 -m unittest discover tests
```

<details>
<summary>watsonx setup notes</summary>

- As of November 2026, Granite chat models are offered in **Dallas (us-south)** and **Frankfurt (eu-de)** only. Set `WATSONX_URL` to match, e.g. `https://us-south.ml.cloud.ibm.com`.
- The API key, watsonx.ai Studio, watsonx.ai Runtime and project must all be in the same IBM Cloud account. The Runtime and project must also be in the same region.
- Attach the Runtime to the project in the **IBM watsonx** portal (Project → Manage → Services & integrations).
- Use an IBM Cloud **API key** (Manage → Access (IAM) → API keys), not a temporary access token.
- The default model is `ibm/granite-4-h-small`. Change it with `WATSONX_MODEL_ID`.
</details>

## Demo

| Patient | Scenario |
|---|---|
| **Maria Lopez**, lumbar MRI | Denied for thin evidence, then the agent finds better evidence, appeals, and is approved |
| **James Carter**, knee MRI | Approved on the first submission (Granite sometimes needs an appeal here too) |
| **Aisha Rahman**, lumbar X-ray | No authorization needed, so the agent closes the case |

Use **Send back to agent** to test the clinician loop, or **Force a denial (demo)** to show the appeal path on demand.

## How it works

```
order → check rules → load policy → read/search chart → draft packet
      → [clinician review] → submit → [wait for payer]
      → approved: close   |   denied: find evidence → draft appeal → [clinician review] → …
```

| Path | Role |
|---|---|
| `priorauth/agent.py` | Agent loop: plan, act, observe. Pauses for humans and the payer, and resumes with memory |
| `priorauth/brains.py` | watsonx Granite client (tool calling), plus an offline mock |
| `priorauth/tools.py` | The 10 tools and their code-level safeguards |
| `priorauth/payer.py` | Mock payer with hidden documentation rules |
| `priorauth/data/` | Synthetic patients and payer policies |
| `priorauth/static/` | Single-page web UI |
| `tests/` | End-to-end flows and safeguard tests |

## Project documents

| Document | |
|---|---|
| [Problem and Solution Statement](docs/problem-solution-statement.md) | The problem, our solution, and its impact |
| [Technology Statement](docs/technology-statement.md) | Architecture, and how it was built |
| [Development log](docs/development-with-bob.md) | Step-by-step build log with examples |
| [Demo video script](docs/demo-video-script.md) | 3-minute walkthrough |
| [Ideation](docs/ideation/) | Ideation-phase submission and one-pager |

## Limitations

This is a prototype. It has no real EHR (FHIR) or payer (ePA) integrations, no authentication, and it keeps cases in memory, so they're lost when the server restarts. All patients, payers and policies are fictional.

# Problem and Solution Statement

## The problem

Before many tests, procedures and drugs are covered, the patient's insurer must approve them. This is called prior authorization. According to the American Medical Association's 2025 Prior Authorization Physician Survey:

- physicians complete an average of **40 prior authorizations per week**;
- the work consumes **13 hours of physician and staff time** each week;
- **95%** of physicians say it delays access to necessary care;
- **79%** report that patients abandon treatment because of it;
- **26%** report that it has led to a serious adverse event.

The work resists simple automation. Each payer publishes its own medical-necessity criteria as natural-language policies, and those policies change. The evidence is buried in unstructured clinical notes. Denials arrive as free-text reasons that each need a tailored response. Staff therefore read charts by hand, copy evidence into portals, chase status, and write appeals. A single vague or missing detail in the documentation can mean a denial and weeks of delay.

## Our solution

**Prior-Auth Copilot** is an AI agent, built on IBM watsonx.ai with Granite, that handles prior authorization from order to approval, including the appeal. When a clinician places an order, the agent:

1. checks whether the payer requires authorization;
2. loads the payer's policy and searches the chart for evidence that meets each criterion;
3. drafts a request packet with a verbatim chart quote for every criterion and a letter of medical necessity;
4. **pauses for clinician review.** The clinician approves the packet or sends it back with a note for revision;
5. submits the packet, then parks the case until the payer responds, even days later;
6. if denied, reads the denial reason, finds evidence that answers it, and drafts a targeted appeal for clinician approval.

This is a goal-driven, multi-step workflow across several systems that adapts to outcomes. In testing, Granite recovered from a denial for "duration of symptoms not documented" by finding "symptoms … have persisted for 3 months" in an earlier note and appealing successfully. It also corrected itself when a safety check rejected a quote that was not in the chart.

## Safe by design

Guardrails are enforced in code, not left to the prompt:

- **No submission without clinician sign-off.**
- **No invented evidence.** Every quote must appear word for word in the cited document, or the packet is rejected.
- **No false closure.** A case can only be marked approved after the payer approves it.
- **Full audit trail** of every tool call and model message.

## Impact

Prior-Auth Copilot turns hours of chart-reading and portal work into minutes of clinician review. For patients, complete submissions and fast appeals mean quicker care and fewer abandoned treatments. Our prototype uses synthetic patients and mock payers. It covers approval on the first submission, denial followed by a successful appeal, and recognizing when no authorization is needed. Next steps are FHIR-based EHR integration and standard electronic prior-authorization interfaces.

*Source: American Medical Association, 2025 Prior Authorization Physician Survey (May 2026).*

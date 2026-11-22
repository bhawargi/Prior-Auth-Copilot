# Technology Statement

## Architecture

Prior-Auth Copilot is an agentic AI application built on **IBM watsonx.ai**. Its reasoning engine is **IBM Granite** (`ibm/granite-4-h-small`), called through the watsonx.ai chat API with native tool calling.

- **Agent loop.** On each step Granite chooses one of ten tools: check authorization rules, load the payer's policy, list, read and search chart documents, prepare a request or appeal, submit, wait for the payer, check status, and close the case. It reads each result and decides the next step.
- **Pause and resume.** A case pauses for clinician review or for the payer's decision. The conversation is stored with the case, so the agent resumes days later with its memory intact.
- **Guardrails in code, not the prompt.** Submission is blocked until a clinician approves. Every evidence quote is checked word for word against the cited chart document. A case cannot be closed as approved unless the payer approved it.
- **Stack.** Python standard library only, a single-page web UI, a mock payer, synthetic charts, and a per-case audit log.

## How we built it: IBM Bob as development partner

The prototype was built with **Bob** (IBM), working as an AI pair programmer using the [Bob Shell](https://bob.ibm.com/docs/shell). I set the direction and made the product decisions; Bob wrote and tested the code. Examples:

- **Design and build.** From our ideation brief, Bob designed the tool-calling agent loop, wrote the ten tools and their guardrails, created three synthetic patients and two payer policies, and built the web UI. I then asked for the case view to be moved to the centre of the screen, and it rearranged the layout.
- **watsonx setup and debugging.** Bob worked through each failure using Watson's own error messages and APIs. It identified that an access token had been supplied instead of an API key, and that the project had no watsonx.ai Runtime attached. It queried the model catalogue and found that Granite chat models are not offered in London, then listed our account's resources and found that our services were all in Dallas.
- **Hardening against real model behaviour.** Running live cases exposed two Granite quirks: tool arguments that were double-encoded, and tool calls emitted as text with a missing closing brace. Bob added parsing to recover both, plus specific retry feedback to the model.
- **Making the demo honest.** After seeing Granite cite "Return in 6 weeks" as evidence of symptom duration, Bob tightened the mock payer's rule. Granite's own mistake now triggers a real denial and appeal.
- **Testing.** Bob wrote 11 unit tests covering the full flows and the guardrails, and ran the demo end to end in a browser.

## Why agentic AI

A rules-based script cannot read free-text payer policies, find "persisted for 3 months" in a clinical note, or answer a specific denial reason. A single prompt cannot run a multi-day workflow across several systems and recover from failures. Prior-Auth Copilot combines Granite's language understanding with an agent's structure, memory and guardrails, and keeps a clinician in the loop.

# Sprint 01 — wire the Phase 0 bot

**Goal:** a runnable LINE bot that produces a real Four Pillars reading, with
the compliance filters on every path, testable locally without LINE or OpenAI
credentials.

**Not the goal:** production-quality Japanese prompts, or deploying anything.
Both wait on the retained practitioner.

---

## Scope

### 1. Port the prototype

Source: `reference/phase0_prototype/` (from the v1.1 board package).

- `bot/app.py` — FastAPI webhook, LINE signature verification, event routing
- `bot/storage.py` — port the JSON-file layer roughly as-is. It is correct for
  50–100 users. `data/` is gitignored.
- `bot/config.py` — load and validate `.env`, fail loudly on a missing key

Port the structure, not the divination logic. The prototype's Western sun-sign
code is replaced by `engine/`.

### 2. Wire the reading path

Every user-facing path follows this order, without exception:

```
screen_input()          → crisis / professional routing, before any model call
build_payload()         → deterministic chart; may raise ManualReviewRequired
format_for_prompt()     → chart as text a practitioner can audit
model call              → interpretation only
screen_output()         → block on 景品表示法 / 霊感商法 patterns
with_disclosure()       → Rule 2, not optional
reply
```

`ManualReviewRequired` must reach a human — a queue entry or an operator
notification, not a generated apology to the user.

### 3. Placeholder prompts

`bot/prompts_ja.py`. Every prompt marked:

```python
# PLACEHOLDER — practitioner to rewrite. Do not ship.
```

The prototype's Japanese system prompt is a reasonable starting shape; keep
its constraint list, which is close to what compliance requires. It is a
scaffold to test the pipeline, not the product.

### 4. Local test runner

`bot/test_local.py` — exercises the full path with a stub model, no LINE
credentials, no network, no spend. Should print the chart, the prompt, the
screening verdicts and the final reply.

### 5. Cost guard

Enforce `MONTHLY_LLM_BUDGET_USD` before every call. Refuse and log when the
cap is reached. Phase 0's entire budget is US$1–3K; a loop must not be able
to touch it.

---

## Definition of done

- [x] `pytest` green, including new tests for the reading path — 298 tests
- [x] `python -m bot.test_local` runs end to end with no credentials
- [x] No code path reaches a model without `screen_input()` first —
      enforced by `ScreenedPrompt`/`ScreeningToken`, not by convention, and
      checked across the package by `bot/tests/test_no_bypass.py`
- [x] No reply reaches a user without `screen_output()` and
      `with_disclosure()` — with one ruled exception, below
- [x] `ManualReviewRequired` routes to a human
- [x] Cost guard tested by simulating cap exhaustion
- [x] No secrets, no real birth data, nothing in `data/` committed
- [x] Every placeholder prompt marked as such — and a test that fails when
      one is added without the marker

## What changed during the sprint

Recorded here so the reasoning is not lost with the conversation.

**The screening/disclosure rule resolved into three cases, not one.**
Generated readings are screened at runtime and always disclosed. Canned copy
is screened in the test suite instead — a runtime filter over our own text
could *block* the crisis message, which is the worst failure available here.
And the crisis and professional replies carry no AI disclosure at all: a
person who has just typed 死にたい should not then read a paragraph about
automated screening. That disclosure is in onboarding and the privacy
policy. See the table in `bot/README.md`.

**The outbound test covers every sender, not just replies.** The daily
reading will be a push. `Transport` declares `push()` abstract while it is
still unused, so the path cannot be added in the wrong shape later.

**Birth time is optional, and the engine no longer invents an hour pillar.**
`compute_chart` returned a 時柱 computed from midnight for any date-only
birth. Requiring the time instead would put a wall at the first question and
make the conversion number unreadable. Recorded as **P6**, with the
divination question it does not answer. The missing-hour rate is
instrumented and also answers **P2**.

**Crisis events are recorded without the message text or the user id.** We
need the rate for the weekly review; we do not need the words. Mental-health
information is likely 要配慮個人情報 and retention goes to counsel in Phase 1.

**The prompt no longer carries the birth datetime.** `format_for_prompt`
included it. A model handed a birth date can recompute a pillar, which is
what E1 exists to prevent, and it is personal information sent to a third
party for no interpretive benefit.

**`/admin/*` requires a token.** The review queue holds birth data; the
prototype's stats endpoint was open.

## Out of scope

Stripe, the 特商法 notice, deployment, the subscription tier, and anything
in the CLAUDE.md "do not build" list.

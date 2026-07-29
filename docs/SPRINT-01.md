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

- [ ] `pytest` green, including new tests for the reading path
- [ ] `python -m bot.test_local` runs end to end with no credentials
- [ ] No code path reaches a model without `screen_input()` first
- [ ] No reply reaches a user without `screen_output()` and `with_disclosure()`
- [ ] `ManualReviewRequired` routes to a human
- [ ] Cost guard tested by simulating cap exhaustion
- [ ] No secrets, no real birth data, nothing in `data/` committed
- [ ] Every placeholder prompt marked as such

## Out of scope

Stripe, the 特商法 notice, deployment, the subscription tier, and anything
in the CLAUDE.md "do not build" list.

# CLAUDE.md — project constitution

Read this before writing code. It encodes decisions that are already made and
should not be relitigated in a coding session.

Full reasoning lives in `docs/AI Fortune Telling - Consolidated Board Package
v2.docx`. Board approved Phase 0 + Phase 1 on 29 July 2026: approximately
US$45,000 over six months, capped at US$70,000.

---

## What this is

A Japanese AI fortune-telling service. Phase 0 exists to answer exactly one
question, as cheaply as possible:

> **Will a Japanese user pay ¥200–500 for an AI-generated reading?**

Everything is subordinate to answering that in 60 days for US$1–3K. If a piece
of work does not move that question forward, it is out of scope for now — say
so rather than building it.

**Target user:** a woman aged 30–50 with a specific relationship question.
Not a twenty-year-old sharing a personality card. Copy, tone and pacing are
built for her.

---

## The one architectural rule

**The language model never calculates.**

Charts, pillars, transits and card draws are computed in `engine/` —
deterministic, pure standard library, unit-tested — and handed to the model as
structured JSON. The model only interprets.

A chart bug is a failing test. A hallucinated birth chart is a public
credibility failure with a user who knows more about 四柱推命 than we do.

Practically:
- Never ask a model to compute, infer or "check" a pillar, element or date.
- Never let chart data reach a prompt except via `bot/chart_service.py`.
- If a feature seems to need model-side calculation, that is a signal the
  engine is missing something. Extend `engine/`, do not shortcut it.

---

## Five rules that constrain the product, not just the copy

From §12 of the board package. Three are enforced in `bot/safety.py`.

1. **Never monetise fear.** No "misfortune is coming, pay to see the remedy"
   mechanic in any form. This is the exact shape the amended 消費者契約法
   makes voidable and it is a company-ending reputational risk. It is also why
   we chose subscription over pay-per-reading as the eventual model.
2. **Disclose AI use** clearly and permanently, on every reading — not in the
   terms of service.
3. **No consequential claims.** No medical, legal, financial or life-or-death
   advice. Crisis language routes to human help and never reaches the model.
4. **No dark patterns.** Hard spending caps, visible balance, one-tap
   cancellation.
5. **Legal review of all user-facing copy** before publication.

`screen_input()` and `screen_output()` are mandatory on every path that
touches a user. A code path that skips one is a defect, not an optimisation.

---

## What is blocked on the retained practitioner

We have not yet hired the practising fortune teller. Until we do:

- **Do not write "final" Japanese prompts.** Placeholder prompts must be
  marked `# PLACEHOLDER — practitioner to rewrite` and must not be presented
  as production-ready. Translated-feeling copy is precisely what a Japanese
  user rejects, and it is the thing we are paying a practitioner to prevent.
- **Do not resolve the four open questions in `docs/DECISIONS.md`.** They are
  school-level disagreements in 四柱推命, not engineering choices. Current
  defaults are placeholders. Changing one without a recorded ruling is a bug.
- **Do not add charts to `engine/tests/fixtures/known_charts.json`** unless
  they were derived independently and by hand. Fixtures captured from our own
  output are worthless as tests.

Flag these when they come up. Do not quietly pick an answer.

---

## Engineering conventions

**Testing.** `pytest` from the repo root. The engine has no runtime
dependencies and must stay that way, so engine tests run in CI without
credentials or network. Every bug fix gets a test that would have caught it.

**The engine is the stable core.** Changes to `engine/` need a test and, for
anything touching divination logic, a note in `docs/DECISIONS.md`. Everything
in `bot/` is Phase 0 disposable — JSON storage, no database, no abstractions
we do not need yet.

**Accuracy honesty.** `engine/solar.py` is accurate to about ±15 minutes of
clock time. Never widen a documented accuracy claim without a test that
guards it. Births near a solar-term boundary raise `ManualReviewRequired` and
must reach a human, not a generated apology.

**Personal information.** Birth data is personal information under
個人情報保護法. `data/` is gitignored. Never log raw birth details, never
commit a fixture containing a real person's data, never send more to a model
than `chart_service.build_payload()` returns.

**Secrets.** `.env` is gitignored; `.env.example` is the template. Never
inline a key, never print one in a log or error.

**Cost.** Respect `MONTHLY_LLM_BUDGET_USD`. Phase 0's entire budget is
US$1–3K; a runaway loop must not be able to touch it. Use the cheap model for
free content, the stronger one only for paid readings, and cache.

---

## Out of scope for Phase 0 — do not build these

Each is a real temptation and each is deliberately deferred:

- Native iOS or Android apps (Phase 2 — the PWA is enough)
- A database (JSON files are correct at 50–100 users)
- Subscription billing (Phase 1 — Phase 0 tests a single ¥200–500 payment)
- Multi-system synthesis, voice, AR (Phase 2+)
- The human reader marketplace (Phase 3)
- Any market other than Japan
- Any personality-IP integration
- Free daily horoscopes as the core product — that is the one flat segment of
  the Japanese market and it is not what we are building

If asked to build something on this list, say it is out of scope and why
before proceeding.

---

## Before anything reaches a real user

1. Engine reviewed by the practitioner against their own charts
2. Prompts written in Japanese by the practitioner, not translated
3. AI disclosure visible and permanent
4. Crisis and professional-domain routing tested end to end, helpline numbers
   confirmed current (there is a `TODO(legal)` on this)
5. 特定商取引法 notice published — required the moment payment is enabled
6. Legal review complete

Friends-and-family smoke testing is fine before all six. A public LINE account
is not.

---

## How to work with me on this

- Prefer the smallest change that answers the Phase 0 question.
- When a decision belongs to the practitioner or to counsel, stop and say so.
- When you find an error in something I wrote or approved, say it plainly.
- Do not add dependencies to `engine/`.
- Do not soften the compliance filters to make a test pass. Fix the prompt.

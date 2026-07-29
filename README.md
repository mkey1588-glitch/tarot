# AI Fortune Telling — Japan Phase 0

Deterministic Four Pillars engine plus a LINE bot, built to answer one
question as cheaply as possible: **will a Japanese user pay ¥200–500 for an
AI-generated reading?**

Strategy, budget and gates live in `docs/AI Fortune Telling - Consolidated
Board Package v2.docx`. Read Section 10 before writing code; read Section 12
before writing any user-facing copy.

> **Status: Phase 0 approved.** The board authorised Phase 0 and Phase 1 on
> 29 July 2026 — approximately US$45,000 over six months, capped at
> US$70,000, with the month-2 and month-6 gates as stop conditions.
> Refinements will be communicated as we go.
>
> Still not authorised: anything beyond Phase 1. The Phase 2 seed is a
> separate board decision, conditional on the month-6 gate.

See `CLAUDE.md` for the working constitution — it is loaded automatically in
Claude Code sessions.

---

## The one architectural rule

**The language model never calculates.**

Charts, pillars and transits are computed in `engine/` — deterministic, pure
standard library, unit-tested. The model receives finished chart data as
structured JSON and does nothing but interpret it in the practitioner's voice.

A chart bug is a failing test. A hallucinated birth chart is a public
credibility failure with a user who knows more about 四柱推命 than we do.
That asymmetry is why the boundary exists, and it is not negotiable.

---

## Layout

```
engine/          Deterministic divination. No LLM, no network, no I/O.
  constants.py     Stems, branches, elements, hidden stems, solar terms
  solar.py         Sun longitude and 節気 boundaries
  bazi.py          Four Pillars chart computation
  tests/           42 tests. These are the acceptance criteria.
bot/             LINE bot, ported from the Phase 0 prototype
  chart_service.py Bridge from engine to prompt
  safety.py        Compliance filter — read the docstring
docs/            Board package and the decisions log
```

## Running

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pytest                      # engine tests need no API keys and no network
```

The engine has **zero runtime dependencies**. `requirements.txt` covers the
bot only, so the engine can be tested in CI without credentials.

---

## What is done, and what is not

**Done and tested**

- Four Pillars engine: year, month, day and hour pillars, elements, hidden
  stems, 日主.
- Correct solar-term handling. The year turns at 立春, not 1 January and not
  Lunar New Year; months turn at their 節 boundary, not the 1st.
- Boundary detection: births too close to a term boundary to compute
  reliably are flagged rather than guessed at.
- Both sides of the 早子時/晩子時 split, and optional 地方時 correction.
- Safety filter: crisis routing, professional-domain redirects, and outbound
  screening for 景品表示法 and 霊感商法 risk.

**Not done — and three of these need the practitioner, not an engineer**

- [ ] **Japanese prompts written with the retained reader.** The prototype's
      prompts are serviceable but were not written by a practitioner. Do not
      translate; have them written. Blocked on the retainer.
- [ ] **Practitioner review of the engine.** 20–30 charts they can vouch for,
      added to `engine/tests/fixtures/known_charts.json`. That review is the
      real acceptance test. Blocked on the retainer.
- [ ] **The school-dependent decisions in `docs/DECISIONS.md`.** Current
      defaults are placeholders. Blocked on the retainer.
- [ ] LINE webhook wiring, ported from the prototype's `app.py`/`handlers.py`.
- [ ] Storage. The prototype's JSON-file layer is fine for Phase 0.
- [ ] 特定商取引法 notice. Required *the moment payment is enabled*.
- [ ] Stripe checkout for the ¥200–500 single reading.
- [ ] Legal review of everything user-facing.

Notice that three of the four blocking items need a person we have not hired.
**Hiring the practitioner is the critical path, not the code.**

---

## Before anything goes in front of a real user

1. Engine reviewed by the practitioner against their own charts.
2. Prompts written in Japanese by the practitioner.
3. AI disclosure visible and permanent (`bot/safety.py`).
4. Crisis and professional-domain routing tested end to end, with the
   helpline numbers confirmed current.
5. 特商法 notice published if payment is live.
6. Legal review complete.

Friends-and-family smoke testing is fine before all six. A public LINE
account is not.

---

## Accuracy, stated plainly

The solar series is accurate to roughly 0.01°, which is about **15 minutes of
clock time**. Spot checks against published NAOJ values land within about 6
minutes. That is irrelevant for almost every chart and decisive for a birth
within minutes of a boundary — which is why those are flagged for manual
review rather than answered.

If exactness at the boundary is ever needed, replace
`sun_apparent_longitude` with a VSOP87 truncation or a lookup table of
official 暦要項 times. Nothing else in the engine changes.

---

## Credit

The LINE bot structure and the Phase 0 validation method come from the
"AI Uranai Board Package v1.1" prototype. Its Japanese system prompt was a
good starting point. Its Western sun-sign logic was the wrong product for
this market and has been replaced by `engine/`.

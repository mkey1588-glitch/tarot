# bot/ — Phase 0 LINE bot

Ported from the "AI Uranai Board Package v1.1" prototype. What carried over,
what did not, and what is still missing.

## Carried over

- **The overall shape.** FastAPI + LINE Messaging API webhook, JSON-file
  storage, per-user free quota, cost logging. Right-sized for 50–100 users.
- **The validation method.** Free deep reading to seed behaviour, then a
  ¥200–500 paywall on a segment to measure conversion. This is the whole
  point of Phase 0.
- **Most of the Japanese system prompt.** Its constraint list — no absolutes,
  hedged phrasing, no medical or legal advice, route serious distress to
  support — was already close to what compliance requires.

## Replaced

- **Western sun signs → `engine/`.** The prototype computed a zodiac sign
  from a birthday. Japanese users expect 四柱推命 and will judge the product
  on chart accuracy. See `chart_service.py`.
- **Advisory constraints → enforced ones.** The prototype asked the model
  not to make absolute claims. `safety.py` checks that it did not, on the way
  in and on the way out. A prompt is a request; a filter is a guarantee.
- **The five-system persona → one.** The prototype's prompt claimed
  占星術・数秘術・タロット・四柱推命・九星気学. A model told it knows five
  systems improvises the four we do not compute.
- **Model-side crisis routing → `screen_input`.** The prototype asked the
  model to handle serious distress. It is handled before the model is
  reachable at all. The prompt keeps the instruction as defence in depth,
  marked as not the enforcement point.
- **「個人の感想です」→ `with_disclosure()`.** A model-authored disclaimer
  is unreviewed user-facing copy, and it is not AI disclosure.
- **LINE SDK v2 → v3**, with our own HMAC signature verification so the
  webhook is testable without credentials.

## The call path

```
inbound message
  └─ screen_input()          crisis → helpline, never a reading
                             medical/legal/financial → professional referral
                             ALLOW mints the ScreeningToken a model call needs
  └─ free-tier quota         after screening, never before
  └─ build_payload()         deterministic chart; raises ManualReviewRequired
  └─ format_for_prompt()     chart as text the practitioner can audit
  └─ ModelGateway.complete() budget guard, then interpretation only
  └─ outbound.reading()      screen_output(), then with_disclosure()
  └─ transport.reply()       accepts an Outbound and nothing else
```

Both screens are mandatory, and they are enforced structurally rather than
by convention:

- `ModelGateway.complete` will not accept a plain string. It requires a
  `ScreenedPrompt`, which requires a `ScreeningToken`, which only
  `safety.screen_input` mints and only on ALLOW. A crisis message has no
  route to a model because the thing a model call needs is never issued.
- A transport will not accept a plain string. It requires an `Outbound`,
  which only `outbound.reading()` and `outbound.canned()` mint. There is no
  `send_text(str)`, deliberately.
- `tests/test_no_bypass.py` parses every module in this package and fails if
  anything but `llm.py` imports an LLM SDK.
- `tests/test_outbound.py` checks **every** public method on **every**
  `Transport` subclass, not just `reply`. The daily reading will be a push,
  and a funnel guarding only replies would not have covered it.

## Disclosure, precisely

Rule 2 says disclose AI use clearly and permanently. That resolves into
three different obligations, not one:

| What | Screened by | Disclosure |
|---|---|---|
| A generated reading | `screen_output()` at runtime | `with_disclosure()`, always |
| Onboarding (`WELCOME`, `HELP`) | the test suite | full disclosure in the copy |
| Crisis and professional referrals | the test suite | **none** — see below |

Canned copy is screened in the test suite rather than at runtime. Running
the filter over our own reviewed text at runtime would let an edit to the
crisis message cause that message to be *blocked*, which is the worst
failure available here. Failing the build instead is strictly better.

The crisis reply carries no AI disclosure, by decision. Someone who has just
typed 死にたい should not then read a paragraph about automated screening: it
makes a message that needs to be warm feel procedural, and "we detected
this" reads as surveillance, which adds shame at the moment we are trying to
remove it. That disclosure lives in onboarding and the privacy policy.

## Personal information

- `data/` is gitignored. Birth data is personal information under
  個人情報保護法.
- **Crisis messages are not stored.** `storage.log_crisis_event(pattern)`
  takes a pattern and nothing else — there is no parameter through which the
  text or the user id could be passed. We need the rate for the weekly
  review; we do not need the words. Mental-health information is likely
  要配慮個人情報. `TODO(legal)`: retention policy in Phase 1.
- The prompt does **not** carry the birth datetime, though
  `build_payload()` returns it. A model handed a birth date can recompute a
  pillar, and it is personal information going to a third party for no
  interpretive benefit.
- `/admin/*` requires `ADMIN_TOKEN`. The review queue holds birth data.
  Unset means those endpoints are disabled, not open.

### 開示 and 削除

Statutory rights under 個人情報保護法, so they are commands in the bot
rather than something a user has to email us about.

- **「データ確認」** reports what is held: birth date, reading count,
  registration date. It reads every store rather than the convenient one —
  a disclosure that misses a file is a false statement about what we keep.
- **「データ削除」** asks once, then **「削除する」** erases. One
  confirmation: making deletion hard is the dark pattern Rule 4 forbids,
  and making it accidental is its own harm. Any other message cancels.

Erasure deletes the profile and strips the birth date from any
manual-review entry, leaving the review id so an operator knows the case
was closed. Funnel and usage rows are **kept with the identifier replaced**
by a fresh random pseudonym — one per erasure, so the journey stays
coherent as one person while becoming unlinkable to them.

That split is deliberate. Deleting the funnel rows would corrupt the one
number Phase 0 exists to produce, and it is not what the right requires:
保有個人データ is data that can identify someone, and a record whose
identifier was replaced by a value we never stored a mapping for cannot.

## The Phase 0 number

`GET /admin/funnel` reports it, or says honestly that we do not have it yet.

```
followed → registered → free_reading → paywall_shown → checkout_started → paid
```

Counts are **distinct users**, not events — before `bot/funnel.py` no event
carried an identifier, so the log could say fourteen readings were delivered
without saying whether that was fourteen people or one person fourteen
times. Reaching a stage counts you at every earlier one, so a dropped event
cannot produce negative conversion.

The headline is `paid_of_offered`: of the users actually shown the paywall,
the share who paid. Read it **per cohort** — board traffic and seed traffic
are different populations and their average describes nobody. That is what
the `board:`/`seed:` labels in `DEMO_ACCESS_CODES` are for.

An empty denominator reports `null`, never `0.0`. "Nobody converted" and
"nobody has been asked" are different claims and Phase 0 is at the second.

**No paywall is shown until every launch gate is met.** Offering a reading we
may not sell would advertise a product that does not exist, and it would put
`paywall_shown` in the funnel for people who were never really asked —
making the one number this phase exists to produce a fiction. Until then a
user who exhausts the free tier is simply told so.

Funnel events are the only ones carrying a user id. `log_crisis_event` takes
a pattern and a timestamp and has no parameter that could accept one.

## Interaction

Quick-reply buttons ride on an `Outbound` (`with_quick`), so they go through
the same funnel as the text. Their labels are user-facing copy registered in
`messages_ja` and screened by the same test as everything else — a button is
smaller than a paragraph and correspondingly easier to forget is copy.

They are **not** attached to a crisis reply or a professional referral. A row
of cheerful suggestions under a helpline would undo the tone the message is
carrying.

Birth dates can arrive three ways: typed, via LINE's date picker (a
`postback`, the one path where the format cannot be got wrong), or wrongly —
and the third now gets the format back rather than the same request again.

Still needed: a **rich menu** (B2). That is an image asset at 2500×1686, not
code, so it waits on a designer rather than on me.

## Still to do

- [ ] `prompts_ja.py` and `messages_ja.py` — **blocked on the retained
      practitioner.** Written in Japanese, not translated. See P5 in
      `docs/DECISIONS.md`. `PROMPTS_ARE_PLACEHOLDERS` is logged at every
      boot and reported at `/health`; flip it when their prompts land.
- [ ] Legal review of all user-facing copy (Rule 5). None has had it.
- [ ] Confirm the two helpline numbers are current (`TODO(legal)` in
      `safety.py`).
- [ ] Stripe checkout. The seam and the gate are in `bot/payments.py`;
      `StripeProvider` is deliberately the last thing written, because it is
      the only part that cannot be tested without a real customer and real
      money. It refuses until all six gates are met.
- [ ] 特定商取引法 notice — required the moment payment is enabled.
- [x] Operator alert for the manual-review queue — `bot/alerts.py`. Set
      `OPERATOR_LINE_USER_ID` and a boundary chart pushes to your own LINE.
      Unset, it degrades to logging and reports itself as **unconfigured**
      at `/admin/stats`, rather than letting a missing alert look like a
      working one. `overdue_manual_reviews` counts entries older than 24h,
      because an alert that fires and is then ignored is the same as no
      alert.
- [ ] `MODEL_PRICES_USD_PER_MTOK` re-confirmed against current pricing.

## Running it

A browser demo, for showing the system to someone who is not going to read
a terminal — the board, and more usefully the practitioner:

```bash
.venv/bin/python -m bot.demo
```

Then open <http://127.0.0.1:8100>. It puts the chart, the exact prompt and
the reply on one screen, which is what the weekly review needs and what a
scrolling log does badly. Presets cover four pillars, three pillars, a
boundary chart, crisis language, a professional referral and an
unregistered user.

It runs the **real** pipeline — same `ReadingService`, same screening, same
`Outbound`, sent through a real `Transport` and rendered from what that
transport received. A mock would only prove we can write HTML. It uses the
stub model and cannot spend money; `--live` calls the real one and is still
budget-guarded.

It is a window onto the product, not the product. Phase 0 ships a LINE bot,
and nothing on that page has had legal review.

And the same thing in a terminal:

```bash
python3 -m bot.test_local
```

Walks the whole pipeline with a stub model: no LINE account, no API key, no
network, no spend. Prints the chart, the prompt, both screening verdicts and
the final reply for nine scenarios.

It also needs no third-party packages — it runs on a bare system `python3`
outside the virtualenv, because nothing on this path imports FastAPI, the
LINE SDK or the OpenAI client. That is worth knowing when you want to check
the pipeline on a machine that has not been set up.

The test suite does need the virtualenv, for FastAPI's test client:

```bash
.venv/bin/python -m pytest
```

## Cost

The prototype measured well under $5/month at 1,000 MAU on a small model.
`MONTHLY_LLM_BUDGET_USD` is checked inside `ModelGateway.complete` before
the transport is touched, against the worst case the call could cost — not
afterwards against what it did cost, which is a report rather than a cap.

## A note on the preview pane

`.claude/launch.json` **attaches** to an already-running server rather than
starting one. It has to: on macOS `~/Documents` is a TCC-protected location,
and the preview launcher does not inherit the Files-and-Folders grant that a
terminal has, so it cannot read anything in this repo — not `.venv`, not even
`scripts/run_demo.py`. The failure is `[Errno 1] Operation not permitted` and
it is a permission on the directory, not a fault in the config.

So start the server yourself, then attach:

```bash
python3 scripts/run_demo.py --port 8100
```

`scripts/run_demo.py` runs under any Python 3.9+ on the machine and finds the
venv's packages itself, so it needs no activation.

To make the preview pane start the server on its own instead, grant the app
access to your Documents folder in System Settings → Privacy & Security, or
move the repo somewhere outside `~/Documents`, `~/Desktop` and `~/Downloads`.

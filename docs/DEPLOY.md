# Deploying the shared demo

This deploys `bot/demo.py` — a window onto the reading pipeline — so it can
be sent to someone. It does **not** deploy the LINE bot.

---

## Read this first: who you are allowed to send it to

CLAUDE.md, "Before anything reaches a real user", lists six gates and then
draws the line they sit on:

> Friends-and-family smoke testing is fine before all six.
> A public LINE account is not.

**The board is friends-and-family.** Send them the link today.

**Seed users are not.** A seed user hands us their real birth date and reads
generated Japanese, and right now that Japanese was written by an engineer,
has had no legal review, and sits in a category where localised emotional
register is one of only three defensible positions we have (§5.3). Showing it
to the cohort whose reaction we intend to *measure* spends that cohort on a
version of the product we already know is not the one.

`/readiness` reports both thresholds and is checked by machine, not by
memory — `bot/readiness.py` reads `docs/DECISIONS.md` and `bot/safety.py`
rather than a flag someone set. Today:

| gate | state |
|---|---|
| 1. Engine reviewed by the practitioner | ✗ P1–P6 all pending |
| 2. Prompts written by the practitioner | ✗ placeholders |
| 3. AI disclosure visible and permanent | ✓ |
| 4. Crisis routing + helplines confirmed | ✗ `TODO(legal)` |
| 5. 特商法 notice | ✓ n/a — payment disabled |
| 6. Legal review of user-facing copy | ✗ |

Gates 3 and 4 are marked as blocking even a board demo, because a board
member forwards a link and because anyone at all might type 死にたい into a
box. Gate 4 is currently the one to close first — see "Before seed users".

---

## What the deployment does and does not keep

- **Birth dates are not stored.** The web form carries them on every
  request, so there is no reason to keep them. There is one exception: a
  chart on a solar-term boundary writes a review-queue entry containing the
  birth date, because a human has to look at it.
- **Questions are not stored.** Crisis detection records which pattern fired
  and the timestamp — never the text, never the session.
- **Sessions are in memory.** A restart logs everyone out and leaves nothing
  at rest.
- **Storage is ephemeral** when shared, unless you set `DEMO_PERSIST=true`.

---

## Configuration

| variable | required | what it does |
|---|---|---|
| `DEMO_ACCESS_CODES` | **yes, to share** | `board:CODE,seed:CODE`. Cohorts appear in the event log so board and seed sessions can be told apart — the Phase 0 number depends on that distinction. |
| `MONTHLY_LLM_BUDGET_USD` | yes | Hard cap, checked before every call against the worst case that call could cost. |
| `OPENAI_API_KEY` | only for `--live` | Without it the demo runs the stub and cannot spend. |
| `DEMO_PERSIST` | no | `true` keeps storage across restarts. Default is ephemeral. |
| `ADMIN_TOKEN` | no | Only used by the LINE app's `/admin/*`. |
| `LEGAL_REVIEW_COMPLETED_ON` | no | Set to the date counsel signed off. Read by `/readiness`. Not for an engineer to set. |

Generate codes with something that is not a word:

```bash
python3 -c "import secrets; print('board:'+secrets.token_urlsafe(9)+',seed:'+secrets.token_urlsafe(9))"
```

**The demo refuses to start** bound to anything but loopback without
`DEMO_ACCESS_CODES`. That is not a warning you can click through: an unlisted
URL is not access control, and this page takes birth dates.

---

## Run it

Locally, exactly as it will run when deployed:

```bash
docker build -t uranai-demo . && docker run --rm -p 8100:8100 -e DEMO_ACCESS_CODES="board:try-me-1234" uranai-demo
```

Any container host works — Fly, Railway, Render, Cloud Run. There is no
database, no volume and no build step beyond `pip install`. Set the
variables above, point the platform at the `Dockerfile`, and give it `$PORT`.

For a live model, add `OPENAI_API_KEY` and change the command to
`python -m bot.demo --live`. It still goes through the budget guard.

I have not deployed this anywhere — no account of yours is involved, and
choosing a host and a region for a service that touches Japanese personal
information is your call, not mine.

---

## Before seed users

In the order I would do them:

1. **Close gate 4.** The helpline numbers and, more importantly, their
   *ordering* need counsel's eye. The 24-hour toll-free line is listed first
   because the other one is a paid ナビダイヤル running roughly 10:00–22:00
   with hours that vary by prefecture — someone in crisis at 3am would have
   reached nothing and been charged for it. That ordering was wrong until
   this sprint and it is the kind of thing that should not depend on an
   engineer noticing.
2. **Get the practitioner's prompts** (gates 1 and 2, P5). Until then the
   demo shows the pipeline, not the product.
3. **Legal review of user-facing copy** (gate 6) — including the privacy
   notice at `/privacy`, which is itself placeholder text.
4. Only then point it at people whose reaction you intend to count.

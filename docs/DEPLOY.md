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
- **Sessions are signed cookies**, not server state. Nothing about a visitor
  is held at rest, and the session survives a redeploy or a second instance —
  which matters because the session is what enforces the access code.
- **Storage is ephemeral** when shared, unless you set `DEMO_PERSIST=true`.

---

## Configuration

| variable | required | what it does |
|---|---|---|
| `DEMO_ACCESS_CODES` | **yes, to share** | `board:CODE,seed:CODE`. Cohorts appear in the event log so board and seed sessions can be told apart — the Phase 0 number depends on that distinction. |
| `MONTHLY_LLM_BUDGET_USD` | yes | Hard cap, checked before every call against the worst case that call could cost. |
| `OPENAI_API_KEY` | only for `--live` | Without it the demo runs the stub and cannot spend. |
| `DEMO_SESSION_SECRET` | **yes on serverless** | Signs the session cookie. Without a stable value each instance signs differently and visitors are logged out at random. Generated per-process when unset, which is fine for one container only. |
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

## A Cloudflare URL, today

```bash
./scripts/share_tunnel.sh
```

Starts the demo in shared mode and points a Cloudflare Quick Tunnel at it,
then prints a `https://<something>.trycloudflare.com` URL and freshly
generated access codes. Send the URL and the `board:` code.

Everything works exactly as it does locally, because it *is* the local
process: real budget guard, real per-visitor quota, real review queue. The
cost is that the link is alive only while that terminal is open and the Mac
is awake, and a new URL is issued each run.

`cloudflared` has to be installed first. This machine has no Homebrew or npm,
so the script prints the one-line download and stops rather than fetching a
binary onto your PATH on your behalf.

**`--shared` is not optional here.** The tunnel connects to `127.0.0.1`, so
the server cannot tell from its own socket that it has been republished to
the internet. Without that flag it would conclude it was private and drop the
access-code requirement at exactly the moment the page became public. The
script always passes it; if you start the demo by hand and tunnel to it
yourself, pass it too.

## Why not Cloudflare Pages or Workers

Two separate reasons, either one sufficient.

The Workers runtime is V8 with Python via Pyodide. It cannot run FastAPI, and
the only way around that would be reimplementing `engine/` in JavaScript — a
second implementation of the chart maths, which is the one thing CLAUDE.md
rules out. A chart bug is a failing test; two chart implementations drifting
apart is a wrong reading nobody notices.

And it is serverless, so it has the same state problem as Vercel below.

Cloudflare **Containers** does run this image and is always-on, but needs a
Workers Paid plan. If you want that rather than the tunnel, it is a
`wrangler.jsonc` away — ask and I will write it.

## Vercel

It deploys, with two things removed rather than pretended.

`api/index.py` is the entrypoint — **not** `bot/app.py`, which is what
Vercel's autodetection suggests. That module is the LINE webhook and its
`app` is `None` on purpose so it imports without credentials; deploying it
would deploy nothing. `pyproject.toml` points at the right one.

Set both of these in the Vercel project:

```
DEMO_ACCESS_CODES     board:<code>,seed:<code>
DEMO_SESSION_SECRET   python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

Startup refuses without either.

`pyproject.toml` carries a `[project]` table because Vercel's builder runs
`uv lock`, which fails with *"No `project` table found"* without one, and
`[tool.uv] package = false` because otherwise uv tries to build the project
and there is no build backend. `requirements.txt` remains the source of
truth for the full application; the `[project]` dependencies list only what
the serverless demo imports at module load, which is `fastapi` and nothing
else.

**No live model there, by construction.** `MONTHLY_LLM_BUDGET_USD` is
enforced by summing `data/llm_usage.jsonl`, which is empty on every cold
start on a serverless filesystem — so the cap would read `$0` spent for ever
and stop being a cap. Rather than pretend otherwise, `create_demo_app`
refuses to build a billable model when it detects an ephemeral filesystem
(from Vercel's own `VERCEL` variable). Spending is impossible instead, which
is a promise the platform can keep.

**Sessions are signed cookies**, so the access gate survives instances
cycling. That is why `DEMO_SESSION_SECRET` is mandatory: with a per-process
generated key, each instance signs differently and visitors are logged out at
random — and the session is what enforces the access code.

What is still lost, and is visible in the UI rather than hidden: the
free-tier quota is per instance, and the manual-review queue does not
survive the request that wrote it. The privacy notice changes wording on
these hosts accordingly, because the standing promise that a person will
follow up on a boundary chart is not one this deployment can keep.

Nothing that makes a reading correct is affected. The chart still comes from
`engine/`, `screen_input` still gates the model, `screen_output` and the
disclosure still gate the reply.

## GitHub → Render

No local Docker or CLI needed. Render reads `render.yaml` and the
`Dockerfile` straight from the repository.

**Make the GitHub repository private.** `CLAUDE.md` carries the board's
approved budget and cap and `docs/` carries the strategy. `reference/` and the
board PDF are gitignored and will not be pushed — the `no-secrets` CI job
checks exactly that — but the rest is not for a public repo.

```bash
git remote add origin git@github.com:<you>/<repo>.git
git push -u origin main sprint-01
```

Then in Render: **New → Blueprint**, pick the repository, and it reads
`render.yaml`. Set `DEMO_ACCESS_CODES` in the dashboard when prompted — it is
marked `sync: false` so it is never committed:

```bash
python3 -c "import secrets; print('board:'+secrets.token_urlsafe(9)+',seed:'+secrets.token_urlsafe(9))"
```

Give the board the URL and the `board:` code. `/health` reports what is
actually deployed — model, gates met, whether access is gated — without
opening the page.

Two notes on the free plan: it idles after inactivity and takes ~30 seconds to
wake, so the first person to open the link waits; and the region is set to
Singapore, the nearest Render region to Japan. Move to starter if that first
impression matters.

Railway, Fly and Cloud Run work the same way from the same `Dockerfile`; only
the dashboard differs.

## Running a live model

By default the deployment runs the stub, which cannot spend anything. To use a
real model set `OPENAI_API_KEY` and change the Docker command to
`python -m bot.demo --live`.

Be clear about what that adds. It does not show the product's voice — the
prompts are still placeholders written by an engineer, and the voice is what a
board member forms an impression from. It shows that the pipeline produces a
real reading from a real chart. `MONTHLY_LLM_BUDGET_USD` is $5 in
`render.yaml`, checked before every call against the worst case that call
could cost.

## Locally, the same image

```bash
docker build -t uranai-demo . && docker run --rm -p 8100:8100 -e DEMO_ACCESS_CODES="board:try-me-1234" uranai-demo
```

Or without Docker:

```bash
python3 scripts/run_demo.py --port 8100
```

I have not deployed this anywhere and no account of yours is involved.
Choosing a host and region for a service that touches Japanese personal
information is your call.

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

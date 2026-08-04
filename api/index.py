"""
Vercel entrypoint for the demo.

Vercel imports a module-level ASGI app, which is the opposite of how the rest
of this codebase is wired — `create_demo_app` takes its collaborators so the
tests can build one with no credentials. So the composition happens here, in
the one file whose job is to be imported by a platform, and nowhere else.

NOT `bot/app.py`. Vercel's error message suggests that module because it
finds a name called `app` in it, but that is the LINE webhook, and the name
is `None` on purpose so the module imports without credentials. Deploying it
would deploy nothing.

WHAT IS AND IS NOT TRUE ON THIS PLATFORM
----------------------------------------
The filesystem does not survive between requests, and several instances run
at once. `bot/config.py` detects that from Vercel's own `VERCEL` variable and
`create_demo_app` reacts to it:

  * A billable model is refused outright. The budget guard enforces
    MONTHLY_LLM_BUDGET_USD by summing the usage log, which is empty on every
    cold start here — so the cap would read $0 spent for ever. Spending is
    made impossible instead, which is a promise that can actually be kept.
  * Sessions are signed cookies, so the access gate survives instances
    cycling. DEMO_SESSION_SECRET must be set or startup refuses.
  * The free-tier quota and the manual-review queue are per-instance and do
    not persist. Both are visible in the UI and neither is load-bearing for a
    demo, but see the privacy notice — a boundary chart cannot promise a
    human will follow up here.

Everything that makes a reading correct is unaffected: the chart still comes
from engine/, screen_input still gates the model, screen_output and the
disclosure still gate the reply.

Required environment variables in the Vercel project:

    DEMO_ACCESS_CODES     board:<code>,seed:<code>
    DEMO_SESSION_SECRET   python3 -c "import secrets; print(secrets.token_urlsafe(32))"
"""

import logging
import os

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from bot.config import Config
from bot.demo import NotShareable, create_demo_app

logger = logging.getLogger("uranai.vercel")

REQUIRED = ("DEMO_ACCESS_CODES", "DEMO_SESSION_SECRET")


def _misconfigured(summary: str, detail: str) -> FastAPI:
    """A deployment that will not serve the demo, and says why.

    `create_demo_app` refuses by raising, which is right at a terminal where
    the traceback is in front of you. On a serverless platform an exception
    at import becomes FUNCTION_INVOCATION_FAILED — a 500 with no message,
    identical for a missing variable and a genuine bug, and readable only by
    someone who knows to open the platform's log viewer.

    So the refusal still holds — nothing below serves a reading, and the
    access gate is not bypassed — but it explains itself at the URL the
    operator is already looking at. 503, so no monitor mistakes it for
    working.

    Only variable *names* appear here, never values.
    """
    missing = [name for name in REQUIRED if not os.getenv(name)]
    logger.error("refusing to start: %s", summary)

    fallback = FastAPI(title="AI Uranai — misconfigured")

    @fallback.get("/{_path:path}", response_class=HTMLResponse)
    def explain(_path: str = ""):
        rows = "".join(
            f"<li><code>{name}</code> — "
            + ("<b>not set</b>" if name in missing else "set")
            + "</li>"
            for name in REQUIRED
        )
        return HTMLResponse(status_code=503, content=f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>Not configured</title>
<style>body{{font:15px/1.7 -apple-system,BlinkMacSystemFont,sans-serif;
max-width:640px;margin:12vh auto;padding:0 24px;color:#1c1b19}}
code{{background:#f0eeea;padding:1px 5px;border-radius:4px}}
h1{{font-size:19px}} .why{{color:#6b6862;font-size:14px}}</style></head>
<body>
<h1>This deployment is not configured, so it is not serving anything.</h1>
<p>{summary}</p>
<ul>{rows}</ul>
<p class="why">{detail}</p>
<p class="why">Set the missing variables in the platform's environment
settings — for all environments, not just Preview — and redeploy. Nothing is
served until then: this page collects birth dates, and an unconfigured
deployment would have no access gate.</p>
</body></html>""")

    return fallback


try:
    _config = Config.from_env()
    # shared=True unconditionally. This file only ever runs because a
    # platform imported it, and that means the page is on the internet.
    app = create_demo_app(_config, live=False, shared=True)
except NotShareable as refusal:
    app = _misconfigured(str(refusal),
                         "This is the deployment refusing to start, not a "
                         "crash. It is deliberate.")
except Exception as exc:  # pragma: no cover - genuine startup bug
    # Not expected. Report the type only: an exception message can carry
    # configuration values, and this page is public.
    app = _misconfigured(
        f"Startup failed with {type(exc).__name__}.",
        "This one is a bug rather than a missing setting. The platform's "
        "function log has the traceback.")

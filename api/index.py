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

from bot.config import Config
from bot.demo import create_demo_app

_config = Config.from_env()

# shared=True unconditionally. This file only ever runs because a platform
# imported it, and that means the page is on the internet.
app = create_demo_app(_config, live=False, shared=True)

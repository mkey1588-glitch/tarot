# The shared demo (bot/demo.py), not the LINE bot.
#
# Read docs/DEPLOY.md before deploying this. In particular: the demo refuses
# to bind to anything but loopback unless DEMO_ACCESS_CODES is set, and that
# refusal is deliberate — this page takes birth dates.

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY engine/ engine/
COPY bot/ bot/
COPY docs/DECISIONS.md docs/DECISIONS.md
COPY CLAUDE.md .

# bot/readiness.py reads docs/DECISIONS.md and bot/safety.py to decide which
# launch gates are met. Both are copied above, deliberately: a build that
# dropped them would report a readiness it had not earned.

# Storage is ephemeral by default when shared, so the container needs no
# volume. Set DEMO_PERSIST=true and mount one only if you have decided to
# keep what visitors type — see the privacy notice in docs/DEPLOY.md.

RUN useradd --create-home --uid 10001 uranai
USER uranai

ENV HOST=0.0.0.0 \
    PORT=8100
EXPOSE 8100

# --live is NOT set. The container runs the stub model unless you change
# this, so a misconfigured deployment cannot spend money.
CMD ["python", "-m", "bot.demo"]

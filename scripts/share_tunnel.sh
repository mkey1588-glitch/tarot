#!/usr/bin/env bash
#
# Put the demo on a public Cloudflare URL, for the board and the cofounder.
#
#   ./scripts/share_tunnel.sh
#
# Starts the demo in SHARED mode and points a Cloudflare Quick Tunnel at it.
# Prints the public URL and the access codes, then stays in the foreground —
# Ctrl-C stops both, and the link dies with them.
#
# WHY --shared MATTERS HERE
# -------------------------
# The tunnel connects to 127.0.0.1 and republishes it to the internet. The
# server cannot tell that from its own socket, so binding to loopback proves
# nothing about who can reach it. Without --shared the app would conclude it
# was private and drop the access-code requirement at exactly the moment the
# page became public. This script always passes it.
#
# WHAT THIS IS NOT
# ----------------
# Not a deployment. The link works only while this terminal is open and this
# Mac is awake. For something that survives a closed laptop, see
# docs/DEPLOY.md.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PORT="${PORT:-8100}"
PYTHON="${PYTHON:-python3}"

# --- cloudflared -----------------------------------------------------------

if ! command -v cloudflared >/dev/null 2>&1; then
  cat <<'MSG'
cloudflared is not installed, and this machine has no Homebrew or npm to
install it with.

Install it yourself, then run this script again. Either:

  Download from Cloudflare directly (~35 MB, official release):
    curl -Lo /tmp/cloudflared.tgz \
      https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-darwin-arm64.tgz
    tar -xzf /tmp/cloudflared.tgz -C /usr/local/bin
    chmod +x /usr/local/bin/cloudflared

  (use cloudflared-darwin-amd64.tgz on an Intel Mac — `uname -m` says which)

Or install Homebrew first and `brew install cloudflared`.

I have deliberately not downloaded this for you: fetching a binary and
putting it on your PATH is a change to your machine, and it should be your
keystroke.
MSG
  exit 1
fi

# --- Access codes ----------------------------------------------------------

if [ -z "${DEMO_ACCESS_CODES:-}" ]; then
  BOARD_CODE="$("$PYTHON" -c 'import secrets; print(secrets.token_urlsafe(9))')"
  SEED_CODE="$("$PYTHON" -c 'import secrets; print(secrets.token_urlsafe(9))')"
  export DEMO_ACCESS_CODES="board:${BOARD_CODE},seed:${SEED_CODE}"
  GENERATED=1
else
  GENERATED=0
fi

cleanup() {
  [ -n "${DEMO_PID:-}" ] && kill "$DEMO_PID" 2>/dev/null || true
  [ -n "${TUNNEL_PID:-}" ] && kill "$TUNNEL_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

# --- The app ---------------------------------------------------------------

echo "starting the demo on 127.0.0.1:${PORT} (shared mode, stub model)…"
"$PYTHON" scripts/run_demo.py --port "$PORT" --shared >/tmp/uranai-demo.log 2>&1 &
DEMO_PID=$!

for _ in $(seq 1 30); do
  if curl -fsS "http://127.0.0.1:${PORT}/health" >/dev/null 2>&1; then break; fi
  sleep 0.5
done

if ! curl -fsS "http://127.0.0.1:${PORT}/health" >/dev/null 2>&1; then
  echo "the demo did not come up. Log:"
  tail -20 /tmp/uranai-demo.log
  exit 1
fi

# --- The tunnel ------------------------------------------------------------

echo "opening a Cloudflare tunnel…"
cloudflared tunnel --url "http://127.0.0.1:${PORT}" >/tmp/uranai-tunnel.log 2>&1 &
TUNNEL_PID=$!

PUBLIC_URL=""
for _ in $(seq 1 60); do
  PUBLIC_URL="$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' /tmp/uranai-tunnel.log 2>/dev/null | head -1 || true)"
  [ -n "$PUBLIC_URL" ] && break
  sleep 0.5
done

if [ -z "$PUBLIC_URL" ]; then
  echo "the tunnel did not report a URL. Log:"
  tail -20 /tmp/uranai-tunnel.log
  exit 1
fi

# --- What to send ----------------------------------------------------------

cat <<EOF

────────────────────────────────────────────────────────────────────
  ${PUBLIC_URL}

EOF
if [ "$GENERATED" -eq 1 ]; then
  echo "  board code   ${BOARD_CODE}"
  echo "  seed code    ${SEED_CODE}   (see docs/DEPLOY.md before using this one)"
else
  echo "  codes        from DEMO_ACCESS_CODES in your environment"
fi
cat <<EOF

  model        stub — no spend, and not the product's voice
  storage      ephemeral, wiped when this exits
  live while   this terminal is open and this Mac is awake

  Ctrl-C to stop. The URL is single-use: a new one is issued each run.
────────────────────────────────────────────────────────────────────

EOF

wait "$DEMO_PID"

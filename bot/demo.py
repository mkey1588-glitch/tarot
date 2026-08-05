"""
A local demo of the reading pipeline, in a browser.

    .venv/bin/python -m bot.demo        then open http://127.0.0.1:8100

WHAT THIS IS FOR
----------------
Showing the system to someone who is not going to read a terminal: the
board, and — more usefully — the practitioner we are hiring. It puts the
chart, the exact prompt and the reply on one screen, which is what
CLAUDE.md says the weekly review needs and what a scrolling log does badly.

WHAT THIS IS NOT
----------------
Not the product. Phase 0's product is a LINE bot; this is a window onto it.
Nothing here is a step towards a web front end, and no user-facing copy on
this page has been through legal review.

It runs the **real** pipeline. The same `ReadingService`, the same
`ModelGateway`, the same screening, the same `Outbound`, sent through a real
`Transport` and rendered from what the transport received. It is not a mock,
which is the only reason it is worth showing anyone: a mock would prove that
we can write HTML.

WHY IT CANNOT SPEND MONEY BY DEFAULT
------------------------------------
`create_demo_app` builds a `StubModel` unless explicitly passed `--live`.
There is no code path here that constructs an OpenAI client on its own, and
`--live` still goes through the budget guard.

Server-rendered HTML with inlined CSS and no JavaScript: no template engine,
no CDN, no new dependency, and it works with no network.
"""

from __future__ import annotations

import argparse
import atexit
import base64
import hashlib
import hmac
import html
import logging
import secrets
import shutil
import tempfile
import time
from datetime import datetime
from typing import Dict, Optional

from urllib.parse import parse_qs

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from bot import readiness
from bot import prompts_ja
from bot.chart_service import ManualReviewRequired, build_payload, format_for_prompt
from bot.config import Config, load_env
from bot.cost import BudgetGuard
from bot.llm import ModelGateway, StubModel
from bot.outbound import NullTransport, Outbound
from bot.prompts_ja import PROMPTS_ARE_PLACEHOLDERS, startup_warning
from bot.reading import BirthData, ReadingService, ReadingTrace, parse_birth_data
from bot.storage import JST, Storage

logger = logging.getLogger("uranai.demo")

DEMO_USER = "demo-user"
SESSION_COOKIE = "uranai_demo"


def deployed_version() -> dict:
    """What is actually running, for answering "did it deploy?".

    Without this the question is unanswerable from outside whenever a
    release changes nothing visible — which is most of them. Platforms
    publish the commit they built; we read whichever one is present.
    """
    import os

    sha = (os.getenv("VERCEL_GIT_COMMIT_SHA")
           or os.getenv("RENDER_GIT_COMMIT")
           or os.getenv("GIT_COMMIT_SHA")
           or "")
    return {
        "commit": sha[:12] or "unknown",
        "branch": (os.getenv("VERCEL_GIT_COMMIT_REF")
                   or os.getenv("RENDER_GIT_BRANCH") or "unknown"),
        "built_at": os.getenv("VERCEL_DEPLOYMENT_ID", "") or "unknown",
    }


class NotShareable(RuntimeError):
    """Raised rather than serving the demo to the internet without a gate."""


SESSION_TTL_SECONDS = 12 * 60 * 60


class Sessions:
    """Per-visitor sessions, carried in a signed cookie rather than in memory.

    Stateless on purpose. A server-side dict works on one long-lived
    container and fails everywhere else: on any platform that runs several
    instances, or recycles them between requests, a visitor's session lands
    on an instance that has never heard of it and they are silently logged
    out — which, since sessions are what enforce the access code, means the
    gate stops working rather than merely annoying people.

    The cookie carries the cohort and a per-visitor id, signed with
    `DEMO_SESSION_SECRET`. It is signed, not encrypted: nothing in it is
    secret, and the only thing that must not be forgeable is the claim to
    have entered a valid code.

    Without a configured secret one is generated per process, which is fine
    for a single container and useless across instances. `create_demo_app`
    refuses that combination on an ephemeral host rather than shipping a
    gate that opens at random.
    """

    def __init__(self, secret: Optional[str] = None):
        self.secret = (secret or secrets.token_urlsafe(32)).encode("utf-8")
        self.generated_secret = secret is None

    def _sign(self, payload: bytes) -> str:
        digest = hmac.new(self.secret, payload, hashlib.sha256).digest()
        return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")

    def create(self, cohort: str) -> str:
        expires = int(time.time()) + SESSION_TTL_SECONDS
        # The visitor id is random and carried in the cookie, so it is stable
        # for that person across instances and restarts. It is not derived
        # from the signature: this id reaches the event log, and a log that
        # carries anything reconstructible into a session token is a log that
        # can impersonate the people in it.
        visitor = secrets.token_hex(6)
        payload = f"{cohort}|{expires}|{visitor}".encode("utf-8")
        encoded = base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")
        return f"{encoded}.{self._sign(payload)}"

    def get(self, token: Optional[str]) -> Optional[dict]:
        if not token or "." not in token:
            return None
        encoded, _, signature = token.partition(".")
        try:
            padding = "=" * (-len(encoded) % 4)
            payload = base64.urlsafe_b64decode(encoded + padding)
        except (ValueError, TypeError):
            return None

        if not hmac.compare_digest(self._sign(payload), signature):
            return None

        try:
            cohort, expires, visitor = payload.decode("utf-8").split("|")
            if int(expires) < time.time():
                return None
        except (ValueError, UnicodeDecodeError):
            return None

        return {"cohort": cohort, "user_id": f"demo:{cohort}:{visitor}"}

ELEMENT_CLASS = {"木": "wood", "火": "fire", "土": "earth",
                 "金": "metal", "水": "water"}

PRESETS = [
    ("四柱（時刻あり）", "1990-05-15", "07:30", "恋愛運を教えてください"),
    ("三柱（時刻なし・P6）", "1990-05-15", "", "恋愛運を教えてください"),
    ("節気の境界 → 有人対応", "2020-08-07", "", "恋愛運を教えてください"),
    ("危機的な表現", "1990-05-15", "07:30", "もう死にたいです"),
    ("医療に関する質問", "1990-05-15", "07:30", "癌は治りますか"),
    ("未登録（生年月日なし）", "", "", "恋愛運を教えてください"),
]


# --- Page ------------------------------------------------------------------

CSS = """
*,*::before,*::after{box-sizing:border-box}
:root{
  --bg:#f6f5f3; --panel:#fff; --ink:#1c1b19; --muted:#6b6862;
  --line:#e2ded7; --accent:#7a5c9e; --ok:#2f7d5d; --warn:#b06b12;
  --stop:#b3402f; --code:#f3f1ed;
}
@media (prefers-color-scheme:dark){
  :root{--bg:#17161a;--panel:#201f24;--ink:#eceaf0;--muted:#9b96a3;
        --line:#33313a;--accent:#b39ad6;--ok:#6fbf95;--warn:#e0a95c;
        --stop:#e8836f;--code:#191820}
}
body{margin:0;background:var(--bg);color:var(--ink);
  font-family:-apple-system,BlinkMacSystemFont,"Hiragino Sans","Noto Sans JP",
  "Yu Gothic",Meiryo,sans-serif;line-height:1.7;font-size:15px}
.wrap{max-width:1180px;margin:0 auto;padding:24px 20px 64px}
h1{font-size:20px;margin:0 0 2px;letter-spacing:.02em}
.sub{color:var(--muted);font-size:13px;margin:0 0 20px}
.banner{background:var(--stop);color:#fff;padding:10px 16px;border-radius:8px;
  font-size:13px;margin-bottom:20px}
.banner b{letter-spacing:.04em}
.cols{display:grid;grid-template-columns:minmax(0,360px) minmax(0,1fr);
  gap:20px;align-items:start}
@media (max-width:900px){
  .cols{grid-template-columns:minmax(0,1fr)}
  /* The reply is what a phone visitor came to see; the form is how they
     got there. Order it accordingly rather than making them scroll. */
  .cols > div{order:1} .cols > .card:first-child{order:2}
}
@media (max-width:420px){
  .wrap{padding:16px 14px 48px}
  .card{padding:14px}
  .gan{font-size:22px}
  table.chart td,table.chart th{padding:6px 2px}
  .yomi{font-size:9px}
  .meta{gap:10px}
}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;
  padding:18px;margin-bottom:16px}
.card h2{font-size:13px;text-transform:uppercase;letter-spacing:.08em;
  color:var(--muted);margin:0 0 14px;font-weight:600}
label{display:block;font-size:12px;color:var(--muted);margin:12px 0 4px}
input[type=text],textarea,select{width:100%;padding:10px 11px;font:inherit;
  /* 16px is not a style choice: iOS Safari zooms the whole page when a
     field below 16px is focused, so a smaller value makes the birth-date
     box jolt the layout on every iPhone. */
  font-size:16px;background:var(--bg);color:var(--ink);
  border:1px solid var(--line);border-radius:6px}
textarea{min-height:76px;resize:vertical}
button{margin-top:16px;width:100%;padding:11px;font:inherit;font-weight:600;
  background:var(--accent);color:#fff;border:0;border-radius:6px;cursor:pointer}
button:hover{filter:brightness(1.08)}
.presets{display:flex;flex-wrap:wrap;gap:6px;margin-top:4px}
.presets a{font-size:12px;padding:5px 9px;border:1px solid var(--line);
  border-radius:999px;text-decoration:none;color:var(--muted);background:var(--bg)}
.presets a:hover{border-color:var(--accent);color:var(--accent)}
.phone{background:#8b9dc3;background:linear-gradient(160deg,#8fa8c8,#7d8fb3);
  border-radius:12px;padding:16px}
@media (prefers-color-scheme:dark){.phone{background:linear-gradient(160deg,#3a4356,#2b3140)}}
.bubble{background:#fff;color:#1c1b19;border-radius:4px 14px 14px 14px;
  padding:12px 14px;font-size:14px;white-space:pre-wrap;word-break:break-word;
  box-shadow:0 1px 2px rgba(0,0,0,.12)}
.bubble.me{background:#8de055;border-radius:14px 4px 14px 14px;margin:0 0 10px auto;
  max-width:80%}
.stage{display:flex;gap:10px;padding:9px 0;border-bottom:1px dashed var(--line);
  font-size:13px;align-items:baseline}
.stage:last-child{border-bottom:0}
.stage .n{color:var(--muted);font-variant-numeric:tabular-nums;min-width:18px}
.stage .what{flex:1}
.pill{font-size:11px;padding:2px 8px;border-radius:999px;font-weight:600;
  letter-spacing:.03em;white-space:nowrap}
.pill.ok{background:color-mix(in srgb,var(--ok) 18%,transparent);color:var(--ok)}
.pill.stop{background:color-mix(in srgb,var(--stop) 18%,transparent);color:var(--stop)}
.pill.warn{background:color-mix(in srgb,var(--warn) 20%,transparent);color:var(--warn)}
.pill.skip{background:var(--code);color:var(--muted)}
.reason{color:var(--muted);font-size:12px;margin-top:2px}
.tech{display:block;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;
  font-size:11px;color:var(--muted);margin-top:1px}
table.chart{width:100%;border-collapse:collapse;margin-top:4px;font-size:14px}
table.chart th{font-size:11px;color:var(--muted);font-weight:600;padding:6px 4px;
  text-align:center;letter-spacing:.06em}
table.chart td{text-align:center;padding:8px 4px;border-top:1px solid var(--line)}
.gan{font-size:26px;line-height:1.25;display:block}
.yomi{font-size:10px;color:var(--muted);display:block}
.el{font-size:11px;padding:1px 6px;border-radius:4px;display:inline-block;
  margin-top:3px}
.el.wood{background:#dff0d8;color:#2f6b34}.el.fire{background:#f8dcd8;color:#98392c}
.el.earth{background:#f2e6cf;color:#7a5a1e}.el.metal{background:#ececf2;color:#55556b}
.el.water{background:#d8e6f4;color:#28527a}
@media (prefers-color-scheme:dark){
 .el.wood{background:#24361f;color:#9ed49b}.el.fire{background:#3a221e;color:#e8a196}
 .el.earth{background:#352c1a;color:#d8bb7c}.el.metal{background:#2b2b33;color:#c0c0d0}
 .el.water{background:#1e2c3d;color:#9cc4e8}}
.unknown{color:var(--muted);font-size:12px;font-style:italic}
.counts{display:flex;gap:6px;flex-wrap:wrap;margin-top:10px}
details{margin-top:10px;border-top:1px solid var(--line);padding-top:10px}
summary{cursor:pointer;font-size:12px;color:var(--muted);letter-spacing:.04em}
pre{background:var(--code);border:1px solid var(--line);border-radius:6px;
  padding:12px;overflow-x:auto;font-size:12.5px;line-height:1.6;
  font-family:ui-monospace,SFMono-Regular,Menlo,monospace;white-space:pre-wrap;
  word-break:break-word;margin:10px 0 0}
.meta{display:flex;gap:18px;flex-wrap:wrap;font-size:12px;color:var(--muted);
  margin-top:12px}
.meta b{color:var(--ink);font-weight:600;font-variant-numeric:tabular-nums}
.foot{margin-top:28px;font-size:12px;color:var(--muted)}
.foot a{color:var(--accent)}
"""


def page(body: str) -> HTMLResponse:
    return HTMLResponse(f"""<!doctype html>
<html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AI Uranai — pipeline demo</title><style>{CSS}</style></head>
<body><div class="wrap">{body}</div></body></html>""")


def esc(value) -> str:
    return html.escape("" if value is None else str(value))


# --- Fragments -------------------------------------------------------------

def banner(config: Optional[Config] = None) -> str:
    blocking = readiness.blocking(config)
    bits = []
    if PROMPTS_ARE_PLACEHOLDERS:
        bits.append("鑑定文はまだ実務家が書いたものではありません "
                    "(prompts are placeholders, written by an engineer)")
    bits.append(f"未達のゲート {len(blocking)}/6 — "
                f'<a href="/readiness" style="color:inherit">詳細</a>')
    bits.append('<a href="/privacy" style="color:inherit">個人情報の扱い</a>')
    bits.append('<a href="https://github.com/mkey1588-glitch/tarot/blob/main/docs/DEMO_GUIDE.md" target="_blank" rel="noopener" '
                'style="color:inherit">案内</a>')
    return ('<div class="banner"><b>DEMO</b> — ' + " · ".join(bits) +
            "</div>")


def gate_page(rejected: bool) -> str:
    """Shown until an access code is entered.

    The demo is gated rather than unlisted because it takes a birth date,
    and because "nobody will find the URL" has never been true.
    """
    error = ('<div class="reason" style="color:var(--stop)">'
             'コードが違うようです。</div>' if rejected else "")
    return f"""
{header()}
<div class="card" style="max-width:560px">
  <h2>アクセスコード</h2>
  <p style="font-size:14px;margin:0 0 10px">
    このデモは招待制です。お渡ししたコードを入力してください。</p>
  <div class="stage"><span class="what">中では、生年月日から
    <b>命式（四柱推命のチャート）</b>を計算し、AI がそれを解釈した鑑定文を
    お見せします。あわせて、その裏で何が起きたかも並べて表示します。</span></div>
  <div class="stage"><span class="what"><b>入力は保存しません。</b>
    生年月日は命式の計算に使ってその場で破棄し、ご相談の文面も残しません。
    詳しくは下の「個人情報の扱い」をご覧ください。</span></div>
  <div class="stage"><span class="what"><b>これは試作です。</b>
    鑑定文はまだ実務家が書いたものではなく、法務レビューも未了です。</span></div>
  <form method="post" action="/enter">
    <label for="code">コード</label>
    <input type="text" id="code" name="code" autocomplete="off" autofocus>
    {error}
    <button type="submit">入る</button>
  </form>
  <div class="meta"><span><a href="/privacy">個人情報の扱い</a></span>
    <span><a href="/readiness">公開準備状況</a></span>
    <span><a href="https://github.com/mkey1588-glitch/tarot/blob/main/docs/DEMO_GUIDE.md" target="_blank" rel="noopener">案内（英語）</a></span></div>
</div>
<div class="foot">AI が生成する占いの試作です。娯楽・自己理解のためのもので、
医療・法律・投資の判断には使えません。</div>"""


def privacy_notice(config: Config) -> str:
    """What this demo does with what you type. Placeholder copy, but the
    facts in it are checked against the code."""
    persist = ("この配備ではデータをディスクに保存します。"
               if config.demo_persist else
               "この配備では、入力内容はプロセスの再起動で消えます。")

    # On an ephemeral host the review queue does not survive the request that
    # wrote it, so the standing promise that a person will follow up is not
    # one this deployment can keep. Say so rather than leave it standing.
    if config.ephemeral_filesystem:
        review_note = ("<b>この配備は検証用のため、</b>節気の境界にあたる命式の"
                       "控えも保持されません。後日の連絡はできませんので、"
                       "その場合はお手数ですが直接ご連絡ください。")
    else:
        review_note = ("<b>節気の境界にあたる命式のみ、</b>"
                       "人が確認するための控えに生年月日が記録されます。"
                       "自動では鑑定を出さないためで、この控えは上記の"
                       "保存方針に従います。")
    return f"""
<div class="card" style="max-width:720px">
  <h2>個人情報の扱い（デモ）</h2>
  <div class="stage"><span class="what"><b>生年月日は保存しません。</b>
    命式の計算に使い、そのつど破棄します。{persist}</span></div>
  <div class="stage"><span class="what"><b>ご相談の文面は保存しません。</b>
    危機的な表現を検知した場合も、記録するのは「どのパターンが反応したか」と
    時刻だけです。本文もセッションの識別子も残しません。</span></div>
  <div class="stage"><span class="what">{review_note}</span></div>
  <div class="stage"><span class="what"><b>鑑定文は AI が生成します。</b>
    すべての鑑定文にその旨を表示します。</span></div>
  <div class="stage"><span class="what"><b>これは試作です。</b>
    文言の法務レビューは未了で、鑑定文は実務家ではなくエンジニアが書いた
    プレースホルダーです。</span></div>
  <div class="meta"><span><a href="/">戻る</a></span></div>
</div>
<div class="foot">PLACEHOLDER — this notice has not had legal review, and a
privacy notice is exactly the kind of copy that needs it.</div>"""


def readiness_card(config: Optional[Config]) -> str:
    def rows(gate_list):
        return "".join(
            stage("", esc(gate.title),
                  pill("ok", "OK") if gate.met else pill("warn", "未達"),
                  gate.detail)
            for gate in gate_list
        )

    for_real = readiness.ready_for_real_users(config)
    for_ff = readiness.ready_for_friends_and_family(config)

    return f"""
<div class="card" style="max-width:820px">
  <h2>下限 — 誰に見せるにせよ、これは満たしている必要があります</h2>
  {rows(readiness.floor())}
  <div class="reason" style="margin-top:10px">
    六つのゲートには含まれません。リンクは転送されますし、誰であれ
    「死にたい」と書く可能性があるからです。
  </div>
</div>
<div class="card" style="max-width:820px">
  <h2>六つのゲート — CLAUDE.md「Before anything reaches a real user」</h2>
  {rows(readiness.gates(config))}
</div>
<div class="card" style="max-width:820px">
  <h2>判定</h2>
  <div class="stage"><span class="what"><b>ボード・知人への提示</b>
    （friends-and-family）<div class="reason">試作であると伝えたうえで</div>
    </span>{pill("ok", "可") if for_ff else pill("stop", "不可")}</div>
  <div class="stage"><span class="what"><b>シードユーザー・一般公開</b>
    （real users）<div class="reason">反応を数える相手に見せる場合</div>
    </span>{pill("ok", "可") if for_real else pill("stop", "不可")}</div>
  <div class="reason" style="margin-top:10px">
    CLAUDE.md: &ldquo;Friends-and-family smoke testing is fine before all six.
    A public LINE account is not.&rdquo;<br>
    シードユーザーは friends-and-family ではありません。実際の生年月日を預けて、
    生成された日本語を読む相手です。
  </div>
  <div class="meta"><span><a href="/">戻る</a></span></div>
</div>"""


def form(birth_date: str, birth_time: str, question: str, tier: str,
         live: bool, example_index: Optional[int] = None) -> str:
    # Indexed rather than carrying the text: one preset is 死にたい, and a
    # link that puts that in the URL puts it in the access log and the
    # browser history too. Same reason the form is a POST.
    # These run the scenario and are shareable: /example/3 is a link a board
    # member can paste to a colleague. Custom readings deliberately have no
    # URL — see the docstring on the /example route.
    presets = "".join(
        f'<a href="/example/{index}">{esc(label)}</a>'
        for index, (label, _d, _t, _q) in enumerate(PRESETS)
    )
    return f"""
<div class="card">
  <h2>入力</h2>
  <div class="presets">{presets}</div>
  <form method="post" action="/reading">
    <label for="birth_date">生年月日 <span class="unknown">（空欄可）</span></label>
    <input type="text" id="birth_date" name="birth_date" value="{esc(birth_date)}"
           placeholder="1990-05-15" autocomplete="off">
    <label for="birth_time">出生時刻 <span class="unknown">（不明なら空欄 — P6）</span></label>
    <input type="text" id="birth_time" name="birth_time" value="{esc(birth_time)}"
           placeholder="07:30" autocomplete="off">
    <label for="question">ご相談</label>
    <textarea id="question" name="question">{esc(question)}</textarea>
    <label for="tier">ティア</label>
    <select id="tier" name="tier">
      <option value="free"{' selected' if tier == 'free' else ''}>free（無料枠・安いモデル）</option>
      <option value="paid"{' selected' if tier == 'paid' else ''}>paid（有料・強いモデル）</option>
    </select>
    <button type="submit">鑑定する</button>
  </form>
  <div class="meta">
    <span>model: <b>{'live' if live else 'stub'}</b></span>
    <span><a href="/reset">枠をリセット</a></span>
  </div>
  <div class="meta">
    <span><a href="/review">命式の照合</a></span>
    <span><a href="/prompt">プロンプトの検討</a></span>
  </div>
  <div class="reason">上の例はそれぞれ URL があり、そのまま共有できます。
    フォームに入力した鑑定には URL がつきません — 生年月日やご相談が
    アドレスに残らないようにするためです。</div>
</div>"""


def pill(kind: str, label: str) -> str:
    return f'<span class="pill {kind}">{esc(label)}</span>'


def stage(number: str, what: str, marker: str, reason: str = "",
          technical: str = "") -> str:
    """One pipeline row.

    `what` is plain Japanese and `technical` is the function that does it.
    Both are shown, the plain one first: a board member reading
    "screen_input()" learns nothing, and an engineer reading only
    「危機的な表現の判定」 cannot find the code. Naming both costs one line
    and serves both readers, which a toggle would not.
    """
    reason_html = f'<div class="reason">{esc(reason)}</div>' if reason else ""
    tech_html = (f'<code class="tech">{esc(technical)}</code>'
                 if technical else "")
    return (f'<div class="stage"><span class="n">{number}</span>'
            f'<span class="what">{what}{tech_html}{reason_html}</span>'
            f'{marker}</div>')


def pillar_cell(pillar: Optional[dict]) -> str:
    if pillar is None:
        return ('<td><span class="unknown">不明</span><br>'
                '<span class="unknown">三柱</span></td>')
    stem_class = ELEMENT_CLASS.get(pillar["stem_element"], "")
    branch_class = ELEMENT_CLASS.get(pillar["branch_element"], "")
    hidden = "・".join(pillar["hidden_stems"])
    return (f'<td><span class="gan">{esc(pillar["pillar"])}</span>'
            f'<span class="yomi">{esc(pillar["reading"])}</span>'
            f'<span class="el {stem_class}">{esc(pillar["stem_element"])}</span> '
            f'<span class="el {branch_class}">{esc(pillar["branch_element"])}</span>'
            f'<span class="yomi">蔵干 {esc(hidden)}</span></td>')


def chart_card(chart: dict) -> str:
    pillars = chart["pillars"]
    counts = "".join(
        f'<span class="el {ELEMENT_CLASS[element]}">{esc(element)} {count}</span>'
        for element, count in chart["element_counts"].items()
    )
    day_master = chart["day_master"]
    note = ("" if chart["hour_known"] else
            '<div class="reason">出生時刻の申告がないため三柱。'
            '時柱から読む事柄には触れないよう、プロンプトで指示しています。</div>')
    return f"""
<div class="card">
  <h2>命式 — engine が計算（モデルは計算しない）</h2>
  <table class="chart">
    <tr><th>年柱</th><th>月柱</th><th>日柱</th><th>時柱</th></tr>
    <tr>{pillar_cell(pillars['year'])}{pillar_cell(pillars['month'])}
        {pillar_cell(pillars['day'])}{pillar_cell(pillars['hour'])}</tr>
  </table>
  {note}
  <div class="meta">
    <span>日主 <b>{esc(day_master['stem'])}（{esc(day_master['element'])}・{esc(day_master['polarity'])}）</b></span>
    <span>節気 <b>{esc(chart['month_term'])}</b></span>
    <span>年 <b>{esc(chart['solar_year'])}</b></span>
  </div>
  <div class="counts">{counts}</div>
</div>"""


def pipeline_card(trace: ReadingTrace, outcome_name: str,
                  cost_usd: float, review_id: Optional[str]) -> str:
    """The operator's view of what happened, stage by stage.

    Each row names the step in plain Japanese and the function that performs
    it. A board member reading "screen_input()" learns nothing; an engineer
    reading only 「危機的な表現の判定」 cannot find the code. Both readers
    get what they need, which is cheaper than a toggle and cannot get out of
    sync with itself.
    """
    rows = []
    verdict = trace.input_verdict

    rows.append(stage(
        "1", "危機的な表現・専門領域の判定",
        pill("ok", "通過") if verdict == "allow"
        else pill("stop", "振り分け"),
        trace.input_reason or "",
        technical="safety.screen_input()",
    ))

    rows.append(stage(
        "2", "無料枠の確認",
        pill("skip", "未到達") if trace.quota_remaining is None
        else pill("ok", f"残り {trace.quota_remaining}"),
        technical="storage.consume_free_quota()",
    ))

    if trace.chart is None:
        rows.append(stage(
            "3", "命式の計算（AI ではなくエンジンが）",
            pill("warn", "有人対応へ") if outcome_name == "manual_review"
            else pill("skip", "未到達"),
            f"受付番号 {review_id}" if review_id else "",
            technical="engine.compute_chart()",
        ))
    else:
        rows.append(stage("3", "命式の計算（AI ではなくエンジンが）",
                          pill("ok", "完了"),
                          technical="engine.compute_chart()"))

    rows.append(stage(
        "4", "プロンプトの組み立て",
        pill("skip", "未到達") if trace.prompt_user is None
        else pill("ok", "検査済みの入力のみ"),
        technical="prompts_ja.build_reading_prompt()",
    ))

    if trace.model_text is None:
        rows.append(stage(
            "5", "AI による解釈",
            pill("stop", "予算超過") if outcome_name == "budget_exceeded"
            else pill("skip", "呼び出さず"),
            technical="llm.ModelGateway.complete()",
        ))
    else:
        rows.append(stage(
            "5", "AI による解釈", pill("ok", esc(trace.model or "")),
            f"{trace.prompt_tokens}/{trace.completion_tokens} tokens · "
            f"${cost_usd:.6f}",
            technical="llm.ModelGateway.complete()",
        ))

    if trace.output_verdict is None:
        rows.append(stage("6", "出力の検査（景品表示法・霊感商法）",
                          pill("skip", "未到達"),
                          technical="safety.screen_output()"))
    elif trace.output_verdict == "allow":
        rows.append(stage("6", "出力の検査（景品表示法・霊感商法）",
                          pill("ok", "通過"),
                          technical="safety.screen_output()"))
    else:
        rows.append(stage("6", "出力の検査（景品表示法・霊感商法）",
                          pill("stop", "遮断"), trace.output_reason or "",
                          technical="safety.screen_output()"))

    rows.append(stage(
        "7", "AI が生成した旨の表示",
        pill("ok", "付与") if trace.disclosure_appended
        else pill("skip", "対象外"),
        "" if trace.disclosure_appended
        else "定型文には付けません。相談窓口の案内に付けないのは決定事項です。",
        technical="safety.with_disclosure()",
    ))

    prompts = ""
    if trace.prompt_user:
        prompts = f"""
  <details><summary>モデルに渡した内容を全部見る（system + user）</summary>
    <pre>{esc(trace.prompt_system)}</pre>
    <pre>{esc(trace.prompt_user)}</pre>
  </details>"""
    if trace.model_text:
        prompts += f"""
  <details><summary>モデルの生の出力（検査前）</summary>
    <pre>{esc(trace.model_text)}</pre>
  </details>"""

    return f"""
<div class="card">
  <h2>パイプライン — outcome: {esc(outcome_name)}</h2>
  {''.join(rows)}
  {prompts}
</div>"""


def reply_card(message: Outbound, question: str) -> str:
    asked = (f'<div class="bubble me">{esc(question)}</div>'
             if question.strip() else "")
    return f"""
<div class="card">
  <h2>ユーザーに届くもの</h2>
  <div class="phone">
    {asked}
    <div class="bubble">{esc(message.text)}</div>
  </div>
  <div class="meta">
    <span>kind <b>{esc(message.kind)}</b></span>
    <span>chunks <b>{len(message.chunks())}</b></span>
    <span>chars <b>{len(message.text)}</b></span>
  </div>
</div>"""


def header() -> str:
    return ('<h1>AI 占い — パイプラインのデモ</h1>'
            '<p class="sub">The real pipeline, not a mock: same '
            '<code>ReadingService</code>, same screening, same '
            '<code>Outbound</code>, sent through a real transport.</p>')


def footer(config: Config, storage: Storage) -> str:
    guard = BudgetGuard(storage, config.monthly_llm_budget_usd)
    stats = storage.get_stats()
    return (f'<div class="foot">'
            f'今月の LLM 支出 ${guard.month_to_date_usd():.6f} / '
            f'${config.monthly_llm_budget_usd:.2f} · '
            f'命式 {stats["charts_computed"]} 件 '
            f'（時刻なし {stats["charts_without_birth_time"]} 件） · '
            f'有人対応待ち {stats["open_manual_reviews"]} 件'
            f'</div>')


# --- App -------------------------------------------------------------------

def create_demo_app(config: Optional[Config] = None,
                    storage: Optional[Storage] = None,
                    service: Optional[ReadingService] = None,
                    live: bool = False,
                    shared: bool = False) -> FastAPI:
    """Build the demo.

    `shared=True` means this will be reachable by someone other than the
    person who started it, and it changes two things:

      * access codes become mandatory. An unguessable URL is not access
        control, and this page takes birth dates.
      * storage is ephemeral unless DEMO_PERSIST is set, so nothing a
        visitor types outlives the process.
    """
    config = config or Config.from_env({"FREE_TIER_LIMIT": "3"})

    if shared and not config.demo_access_codes:
        raise NotShareable(
            "refusing to share the demo without DEMO_ACCESS_CODES. An "
            "unlisted URL is not access control, and this page collects "
            'birth dates. Set e.g. DEMO_ACCESS_CODES="board:<code>".'
        )

    if config.ephemeral_filesystem:
        # The budget guard sums month-to-date spend out of the usage log. On
        # a host where that log does not survive between requests it reads
        # $0 for ever, so MONTHLY_LLM_BUDGET_USD stops being a cap and
        # becomes a decoration — precisely the failure CLAUDE.md's cost rule
        # exists to prevent.
        #
        # Rather than pretend, spending is made impossible: no billable model
        # here, at all. The cap holds because nothing can be spent, which is
        # a guarantee we can actually keep on this class of platform.
        if live:
            raise NotShareable(
                "refusing to run a billable model on an ephemeral "
                "filesystem. MONTHLY_LLM_BUDGET_USD is enforced by summing "
                "data/llm_usage.jsonl, which does not survive between "
                "requests here, so the cap would silently stop capping. Use "
                "the stub model, or deploy somewhere with a real disk — see "
                "docs/DEPLOY.md."
            )
        if shared and not config.demo_session_secret:
            raise NotShareable(
                "refusing to share on an ephemeral filesystem without "
                "DEMO_SESSION_SECRET. Each instance would sign session "
                "cookies with a different generated key, so visitors would "
                "be logged out at random — and the session is what enforces "
                "the access code. Generate one with: "
                "python3 -c 'import secrets; print(secrets.token_urlsafe(32))'"
            )

    if storage is None:
        if config.ephemeral_filesystem:
            # The only writable path on most of these platforms.
            storage = Storage(tempfile.mkdtemp(prefix="uranai-demo-"))
        elif shared and not config.demo_persist:
            # Nothing a visitor types survives the process. The only birth
            # data written at all is a boundary chart's review-queue entry;
            # see the privacy notice.
            temporary = tempfile.mkdtemp(prefix="uranai-demo-")
            atexit.register(shutil.rmtree, temporary, True)
            logger.info("ephemeral demo storage at %s", temporary)
            storage = Storage(temporary)
        else:
            storage = Storage(config.data_dir / "demo")

    if service is None:
        if live:
            # Explicitly asked for. Still goes through the budget guard.
            config.require_llm()
            from bot.llm import OpenAIModel
            client = OpenAIModel(config.openai_api_key)
        else:
            client = StubModel()
        service = ReadingService(
            storage,
            ModelGateway(client, BudgetGuard(storage,
                                             config.monthly_llm_budget_usd)),
            config,
        )

    app = FastAPI(title="AI Uranai — demo")
    sessions = Sessions(config.demo_session_secret)
    app.state.sessions = sessions

    warning = startup_warning()
    if warning:
        logger.warning(warning)
    for gate in readiness.blocking(config):
        logger.warning("readiness gate not met — %s: %s", gate.key, gate.detail)

    def visitor(request: Request) -> Optional[dict]:
        """The current session, or None. No codes configured means local
        use, where the operator is the only visitor."""
        if not config.demo_access_codes:
            return {"cohort": "local", "user_id": DEMO_USER}
        return sessions.get(request.cookies.get(SESSION_COOKIE))

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request, preset: int = 0, bad: int = 0):
        if visitor(request) is None:
            return page(gate_page(bool(bad)))
        _label, birth_date, birth_time, question = PRESETS[
            preset if 0 <= preset < len(PRESETS) else 0]
        return page(header() + banner(config) +
                    '<div class="cols">' +
                    form(birth_date, birth_time, question, "free", live) +
                    '<div>' + intro() + '</div>'
                    '</div>' + footer(config, storage))

    @app.post("/enter")
    async def enter(request: Request):
        fields = _parse_form(await request.body())
        submitted = (fields.get("code") or "").strip()
        cohort = config.demo_access_codes.get(submitted)
        if cohort is None:
            logger.warning("demo access refused")
            return RedirectResponse("/?bad=1", status_code=303)

        token = sessions.create(cohort)
        storage.log_event("demo_session_started", {"cohort": cohort})
        response = RedirectResponse("/", status_code=303)
        response.set_cookie(
            SESSION_COOKIE, token, httponly=True, samesite="lax",
            secure=request.url.scheme == "https", max_age=SESSION_TTL_SECONDS,
        )
        return response

    @app.get("/health")
    def health():
        """For a platform's health check, and for answering "what is actually
        deployed right now" without opening the page."""
        return {
            "status": "ok",
            "version": deployed_version(),
            "model": "live" if live else "stub",
            "gates_met": readiness.summary(config),
            "ready_for_friends_and_family":
                readiness.ready_for_friends_and_family(config),
            "ready_for_real_users": readiness.ready_for_real_users(config),
            "prompts_are_placeholders": PROMPTS_ARE_PLACEHOLDERS,
            "access_gated": bool(config.demo_access_codes),
        }

    @app.get("/privacy", response_class=HTMLResponse)
    def privacy():
        return page(header() + privacy_notice(config))

    @app.get("/readiness", response_class=HTMLResponse)
    def readiness_page():
        return page(header() + readiness_card(config))

    def render_reading(session: dict, birth_date: str, birth_time: str,
                       question: str, tier: str) -> HTMLResponse:
        birth = _birth_from_form(birth_date, birth_time)
        trace = ReadingTrace()

        # The cohort travels with the reading, so board clicks and seed
        # traffic land in different columns of the funnel.
        service.cohort = session.get("cohort", "demo")
        outcome = service.generate(session["user_id"], question, birth=birth,
                                   tier=tier, trace=trace)

        # Sent through a real Transport, then rendered from what the
        # transport received — so this page cannot show anything that was
        # not cleared to send.
        transport = NullTransport()
        transport.reply("demo-reply-token", outcome.message)
        delivered = transport.sent[-1]["message"]

        body = [header(), banner(config), '<div class="cols">',
                form(birth_date, birth_time, question, tier, live), '<div>',
                reply_card(delivered, question),
                pipeline_card(trace, outcome.outcome, outcome.cost_usd,
                              outcome.review_id)]
        if trace.chart:
            body.append(chart_card(trace.chart))
        body.append('</div></div>')
        body.append(footer(config, storage))
        return page("".join(body))

    @app.get("/example/{index}", response_class=HTMLResponse)
    def example(request: Request, index: int):
        """A preset, run from a shareable URL.

        Only presets get a link. A URL for a custom reading would carry a
        birth date and a question in its query string — into the access log,
        into browser history, into whatever chat app it was pasted into —
        which is exactly what the form being a POST avoids. Presets are our
        own fixtures and describe nobody, so "look at scenario 4" can be a
        link and "look at my mother's chart" cannot.
        """
        session = visitor(request)
        if session is None:
            return RedirectResponse("/", status_code=303)
        if not 0 <= index < len(PRESETS):
            return RedirectResponse("/", status_code=303)
        _label, birth_date, birth_time, question = PRESETS[index]
        return render_reading(session, birth_date, birth_time, question, "free")

    @app.post("/reading", response_class=HTMLResponse)
    async def reading(request: Request):
        session = visitor(request)
        if session is None:
            return RedirectResponse("/", status_code=303)

        # Parsed here rather than with FastAPI's Form(), which needs
        # python-multipart — not a dependency worth adding for a dev tool.
        # A GET form would avoid both, but it would put the user's question
        # in the URL and therefore in the server log and browser history,
        # and that question can be 死にたい.
        fields = _parse_form(await request.body())
        return render_reading(
            session,
            fields.get("birth_date", ""), fields.get("birth_time", ""),
            fields.get("question", ""), fields.get("tier", "free"),
        )

    # --- The practitioner's workbench (gates 1 and 2) --------------------

    @app.get("/review", response_class=HTMLResponse)
    def review_page(request: Request):
        if visitor(request) is None:
            return RedirectResponse("/", status_code=303)
        return page(header() + banner(config) + review_form("") + review_help())

    @app.post("/review", response_class=HTMLResponse)
    async def review_run(request: Request):
        if visitor(request) is None:
            return RedirectResponse("/", status_code=303)
        pasted = _parse_form(await request.body()).get("charts", "")
        return page(header() + banner(config) + review_form(pasted)
                    + review_results(pasted) + review_help())

    @app.get("/prompt", response_class=HTMLResponse)
    def prompt_page(request: Request):
        if visitor(request) is None:
            return RedirectResponse("/", status_code=303)
        return page(header() + banner(config)
                    + prompt_workbench(prompts_ja.SYSTEM_PROMPT,
                                       prompts_ja.READING_PROMPT,
                                       "1990-05-15", "07:30",
                                       "恋愛運を教えてください", live))

    @app.post("/prompt", response_class=HTMLResponse)
    async def prompt_run(request: Request):
        if visitor(request) is None:
            return RedirectResponse("/", status_code=303)
        f = _parse_form(await request.body())
        return page(header() + banner(config) + prompt_workbench(
            f.get("system", ""), f.get("template", ""),
            f.get("birth_date", ""), f.get("birth_time", ""),
            f.get("question", ""), live, run=True, service=service))

    @app.get("/reset")
    def reset(request: Request):
        session = visitor(request)
        if session is not None:
            storage.upsert_user(session["user_id"],
                                {"quota_reset_date": "1970-01-01",
                                 "free_quota_used": 0})
        return RedirectResponse("/", status_code=303)

    return app


def review_form(pasted: str) -> str:
    """Where the practitioner pastes charts they computed by hand."""
    return f"""
<div class="card">
  <h2>命式の照合 — 先生が立てた命式との突き合わせ</h2>
  <form method="post" action="/review">
    <label for="charts">CSV を貼り付けてください</label>
    <textarea id="charts" name="charts" style="min-height:150px;
      font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:13px"
      placeholder="label,birth_date,birth_time,year,month,day,hour,note
例1,1990-05-15,07:30,庚午,辛巳,庚辰,庚辰,
23時台,1985-03-10,23:30,,,癸巳,壬子,子の刻で日柱を変える流派"
      >{esc(pasted)}</textarea>
    <button type="submit">照合する</button>
  </form>
</div>"""


def review_help() -> str:
    return """
<div class="card">
  <h2>相違の見かた</h2>
  <div class="stage"><span class="what"><b>相違のほとんどは不具合ではありません。</b>
    未決の論点で説明がつくものと、こちらの計算精度の範囲に収まるものを
    分けて表示します。</span></div>
  <div class="stage"><span class="what"><b>P1</b> 早子時/晩子時。日柱を23時で
    繰り上げるか、子の刻まで持つか。既定は繰り上げですが仮置きです。</span></div>
  <div class="stage"><span class="what"><b>P2</b> 地方時修正。合う場合、
    どの経度なら合うかを範囲で出します。出生地と照らしてご確認ください。</span></div>
  <div class="stage"><span class="what"><b>精度</b> 節気の境界に近く、
    こちらの誤差（節入りで最大9.2分）の範囲。作法の違いではありません。</span></div>
  <div class="stage"><span class="what"><b>未説明</b> 上記のどれでもないもの。
    こちらで直すべきものは、これだけです。</span></div>
</div>"""


def review_results(pasted: str) -> str:
    """Run the harness and render its findings."""
    import csv as _csv
    import io

    from engine.review import Expectation, PILLARS, review, summarise

    expectations, errors = [], []
    reader = _csv.DictReader(
        line for line in io.StringIO(pasted) if not line.lstrip().startswith("#"))
    for number, row in enumerate(reader, start=2):
        raw_date = (row.get("birth_date") or "").strip()
        if not raw_date:
            continue
        raw_time = (row.get("birth_time") or "").strip()
        try:
            stamp = datetime.fromisoformat(f"{raw_date}T{raw_time or '00:00'}")
        except ValueError:
            errors.append(f"{number} 行目: 日付を読み取れません（{esc(raw_date)}）")
            continue
        expectations.append(Expectation(
            label=(row.get("label") or raw_date).strip(),
            birth_local=stamp, hour_known=bool(raw_time),
            expected={n: (row.get(n) or "").strip() for n in PILLARS
                      if (row.get(n) or "").strip()},
            note=(row.get("note") or "").strip(),
        ))

    if not expectations:
        problem = "<br>".join(errors) if errors else "命式が読み取れませんでした。"
        return f'<div class="card"><h2>結果</h2><div class="reason">{problem}</div></div>'

    findings = review(expectations)
    counts = summarise(findings)

    label = {"agrees": ("ok", "一致"), "P1": ("warn", "P1"),
             "P2": ("warn", "P2"), "precision": ("warn", "精度"),
             "unexplained": ("stop", "未説明")}

    rows = []
    for finding in findings:
        kind, text = label[finding.diagnosis]
        detail = ""
        if not finding.agrees:
            diffs = "　".join(
                f"{n}: 先生 {finding.expectation.expected[n]} / "
                f"engine {finding.computed.get(n)}"
                for n in finding.mismatched)
            detail = f"{diffs}<br>{esc(finding.detail)}"
        rows.append(stage("", esc(finding.expectation.label),
                          pill(kind, text), "", technical=""))
        if detail:
            rows.append(f'<div class="reason" style="margin:-6px 0 8px 28px">'
                        f'{detail}</div>')

    unexplained = counts.get("unexplained", 0)
    verdict = ("未説明はありません。相違はすべて未決の論点か精度で説明がつきます。"
               if not unexplained else
               f"未説明が {unexplained} 件あります。"
               "エンジンの不具合か、まだ実装していない作法です。")

    summary = "　".join(f"{label[k][1]} {v}" for k, v in counts.items())
    error_html = ("".join(f'<div class="reason">{e}</div>' for e in errors)
                  if errors else "")
    return f"""
<div class="card">
  <h2>結果 — {len(findings)} 件</h2>
  {''.join(rows)}
  {error_html}
  <div class="meta"><span>{esc(summary)}</span></div>
  <div class="reason" style="margin-top:8px">{verdict}
    P1・P2 に分類されたものは docs/DECISIONS.md の裁定の材料になります。</div>
</div>"""


def prompt_workbench(system: str, template: str, birth_date: str,
                     birth_time: str, question: str, live: bool,
                     run: bool = False, service=None) -> str:
    """Gate 2: the practitioner writing prompts without an engineer.

    The assembled prompt is shown whether or not a model is connected,
    because that is the half they are actually authoring — the exact text
    the model receives, with a real chart in it. Generating a reading from
    it needs a live model, and the page says so rather than showing stub
    output that would not change however the prompt is edited.
    """
    result = ""
    if run:
        birth = _birth_from_form(birth_date, birth_time)
        if birth is None:
            result = ('<div class="card"><h2>結果</h2><div class="reason">'
                      '生年月日を読み取れませんでした。</div></div>')
        else:
            try:
                payload = build_payload(birth.as_datetime(),
                                        hour_known=birth.hour_known)
            except ManualReviewRequired as needs_human:
                result = ('<div class="card"><h2>結果</h2><div class="reason">'
                          'この命式は節気の境界にあたるため自動では出しません。<br>'
                          f'{esc(needs_human.warnings[0])}</div></div>')
            else:
                chart_text = format_for_prompt(payload)
                try:
                    assembled = template.format(
                        chart=chart_text,
                        hour_note=prompts_ja.hour_note(birth.hour_known),
                        question=question,
                    )
                except KeyError as missing:
                    assembled = (f"[テンプレートの差し込み名が不明です: "
                                 f"{missing}]")

                note = ("" if live else
                        '<div class="reason">現在はスタブ応答のため、'
                        'プロンプトを変えても鑑定文は変わりません。'
                        '文面の検討には下の「組み立てられたプロンプト」を'
                        'お使いください。</div>')
                result = f"""
<div class="card">
  <h2>組み立てられたプロンプト — モデルが受け取る内容そのもの</h2>
  {note}
  <details open><summary>system</summary><pre>{esc(system)}</pre></details>
  <details open><summary>user</summary><pre>{esc(assembled)}</pre></details>
</div>"""

    return f"""
<div class="card">
  <h2>プロンプトの検討 — 実務家用</h2>
  <div class="reason">ここで書いた内容は保存されません。
    固まったものをお送りいただければ、こちらで反映します。</div>
  <form method="post" action="/prompt">
    <label for="system">system プロンプト</label>
    <textarea id="system" name="system" style="min-height:170px;font-size:13px"
      >{esc(system)}</textarea>
    <label for="template">鑑定文のテンプレート
      <span class="unknown">（{{chart}} {{hour_note}} {{question}} が差し込まれます）</span></label>
    <textarea id="template" name="template" style="min-height:130px;font-size:13px"
      >{esc(template)}</textarea>
    <label for="pd">試す生年月日</label>
    <input type="text" id="pd" name="birth_date" value="{esc(birth_date)}">
    <label for="pt">出生時刻（不明なら空欄）</label>
    <input type="text" id="pt" name="birth_time" value="{esc(birth_time)}">
    <label for="pq">ご相談</label>
    <input type="text" id="pq" name="question" value="{esc(question)}">
    <button type="submit">組み立てる</button>
  </form>
</div>
{result}"""


def intro() -> str:
    return """
<div class="card">
  <h2>読み方</h2>
  <div class="stage"><span class="what">左のフォームか、上のプリセットから
    実行してください。実行すると、ユーザーに届く画面と、その裏で何が起きたかを
    並べて表示します。</span></div>
  <div class="stage"><span class="what"><b>命式は engine が計算します。</b>
    モデルは計算しません。プロンプトに生年月日は入りません — 渡すのは
    計算済みの命式だけです。</span></div>
  <div class="stage"><span class="what"><b>時刻は任意です（P6）。</b>
    空欄にすると三柱の命式になり、時柱から読む事柄には触れないよう
    プロンプトで指示します。</span></div>
  <div class="stage"><span class="what"><b>危機的な表現はモデルに届きません。</b>
    screen_input() が先に判定し、届くのは相談窓口の案内です。
    記録するのはパターンと時刻だけで、本文もユーザー ID も保存しません。</span></div>
</div>"""


def _parse_form(body: bytes) -> dict:
    """application/x-www-form-urlencoded, without python-multipart."""
    parsed = parse_qs(body.decode("utf-8"), keep_blank_values=True)
    return {key: values[0] for key, values in parsed.items()}


def _birth_from_form(birth_date: str, birth_time: str) -> Optional[BirthData]:
    combined = f"{birth_date.strip()} {birth_time.strip()}".strip()
    if not combined:
        return None
    return parse_birth_data(combined)


def main() -> None:  # pragma: no cover - entry point
    import os

    parser = argparse.ArgumentParser(description=__doc__)
    # $PORT so a platform that assigns one is not fought with.
    parser.add_argument("--port", type=int,
                        default=int(os.getenv("PORT", "8100")))
    parser.add_argument("--host", default=os.getenv("HOST", "127.0.0.1"),
                        help="0.0.0.0 to serve beyond this machine. Requires "
                             "DEMO_ACCESS_CODES.")
    parser.add_argument("--live", action="store_true",
                        help="call the real model instead of the stub. "
                             "Needs OPENAI_API_KEY; still budget-guarded.")
    parser.add_argument("--shared", action="store_true",
                        default=(os.getenv("DEMO_SHARED", "").lower()
                                 in {"1", "true", "yes"}),
                        help="this server is reachable by someone other than "
                             "you, even though it binds to loopback. Required "
                             "when a tunnel or proxy sits in front of it — see "
                             "below.")
    args = parser.parse_args()

    import uvicorn

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    load_env()
    config = Config.from_env()

    # The bind address is not sufficient to decide this. A Cloudflare Tunnel,
    # ngrok, or any reverse proxy connects to 127.0.0.1 and republishes it to
    # the internet — the process cannot tell from its own socket that this has
    # happened. Binding to loopback therefore proves nothing, and inferring
    # "private" from it would silently drop the access-code requirement at
    # exactly the moment the page became public. So --shared is explicit, and
    # scripts/share_tunnel.sh always passes it.
    shared = args.shared or args.host not in {"127.0.0.1", "localhost", "::1"}

    # Refuses rather than warns. A demo that takes birth dates and serves
    # them to the internet because someone forgot an env var is not a
    # mistake worth leaving available.
    app = create_demo_app(config, live=args.live, shared=shared)

    print(f"\n  AI Uranai demo — http://{args.host}:{args.port}")
    print(f"  model     {'LIVE (billed, budget-guarded)' if args.live else 'stub (no network, no spend)'}")
    print(f"  access    {'codes required — ' + ', '.join(sorted(set(config.demo_access_codes.values()))) if config.demo_access_codes else 'OPEN (loopback only)'}")
    print(f"  storage   {'persistent' if config.demo_persist or not shared else 'ephemeral (wiped on exit)'}")
    # flush: stdout is block-buffered when this is not a terminal, which is
    # exactly the case where someone is reading the log to find out what
    # they just deployed.
    print(f"  readiness {readiness.summary(config)}"
          f"  ·  friends-and-family: "
          f"{'ok' if readiness.ready_for_friends_and_family(config) else 'NO'}"
          f"  ·  real users: "
          f"{'ok' if readiness.ready_for_real_users(config) else 'NO'}\n",
          flush=True)

    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":  # pragma: no cover
    main()

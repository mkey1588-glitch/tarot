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
import html
import logging
from typing import Optional

from urllib.parse import parse_qs

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from bot.config import Config, load_env
from bot.cost import BudgetGuard
from bot.llm import ModelGateway, StubModel
from bot.outbound import NullTransport, Outbound
from bot.prompts_ja import PROMPTS_ARE_PLACEHOLDERS, startup_warning
from bot.reading import BirthData, ReadingService, ReadingTrace, parse_birth_data
from bot.storage import Storage

logger = logging.getLogger("uranai.demo")

DEMO_USER = "demo-user"

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
@media (max-width:900px){.cols{grid-template-columns:minmax(0,1fr)}}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;
  padding:18px;margin-bottom:16px}
.card h2{font-size:13px;text-transform:uppercase;letter-spacing:.08em;
  color:var(--muted);margin:0 0 14px;font-weight:600}
label{display:block;font-size:12px;color:var(--muted);margin:12px 0 4px}
input[type=text],textarea,select{width:100%;padding:9px 11px;font:inherit;
  font-size:14px;background:var(--bg);color:var(--ink);
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

def banner() -> str:
    bits = []
    if PROMPTS_ARE_PLACEHOLDERS:
        bits.append("プロンプトはすべてプレースホルダーです — "
                    "PROMPTS ARE PLACEHOLDERS, written by an engineer, not "
                    "by the practitioner")
    bits.append("no legal review · not for public use · friends-and-family only")
    return (f'<div class="banner"><b>DEMO</b> — ' + " · ".join(bits) + "</div>")


def form(birth_date: str, birth_time: str, question: str, tier: str,
         live: bool) -> str:
    # Indexed rather than carrying the text: one preset is 死にたい, and a
    # link that puts that in the URL puts it in the access log and the
    # browser history too. Same reason the form is a POST.
    presets = "".join(
        f'<a href="/?preset={index}">{esc(label)}</a>'
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
</div>"""


def pill(kind: str, label: str) -> str:
    return f'<span class="pill {kind}">{esc(label)}</span>'


def stage(number: str, what: str, marker: str, reason: str = "") -> str:
    reason_html = f'<div class="reason">{esc(reason)}</div>' if reason else ""
    return (f'<div class="stage"><span class="n">{number}</span>'
            f'<span class="what">{what}{reason_html}</span>{marker}</div>')


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
    rows = []

    verdict = trace.input_verdict
    rows.append(stage(
        "1", "screen_input() — 危機・専門領域",
        pill("ok", "ALLOW") if verdict == "allow" else pill("stop", (verdict or "").upper()),
        trace.input_reason or "",
    ))

    if trace.quota_remaining is None:
        rows.append(stage("2", "無料枠", pill("skip", "not reached")))
    else:
        rows.append(stage("2", "無料枠",
                          pill("ok", f"残り {trace.quota_remaining}")))

    if trace.chart is None:
        marker = (pill("warn", "MANUAL REVIEW") if outcome_name == "manual_review"
                  else pill("skip", "not reached"))
        rows.append(stage("3", "build_payload() — 命式", marker,
                          f"受付番号 {review_id}" if review_id else ""))
    else:
        rows.append(stage("3", "build_payload() — 命式", pill("ok", "OK")))

    if trace.prompt_user is None:
        rows.append(stage("4", "プロンプト組み立て", pill("skip", "not reached")))
    else:
        rows.append(stage("4", "プロンプト組み立て",
                          pill("ok", "ScreenedPrompt")))

    if trace.model_text is None:
        marker = (pill("stop", "BUDGET") if outcome_name == "budget_exceeded"
                  else pill("skip", "not called"))
        rows.append(stage("5", "モデル呼び出し", marker))
    else:
        rows.append(stage(
            "5", "モデル呼び出し", pill("ok", esc(trace.model or "")),
            f"{trace.prompt_tokens}/{trace.completion_tokens} tokens · "
            f"${cost_usd:.6f}",
        ))

    if trace.output_verdict is None:
        rows.append(stage("6", "screen_output()", pill("skip", "not reached")))
    elif trace.output_verdict == "allow":
        rows.append(stage("6", "screen_output()", pill("ok", "ALLOW")))
    else:
        rows.append(stage("6", "screen_output()", pill("stop", "BLOCK"),
                          trace.output_reason or ""))

    rows.append(stage(
        "7", "with_disclosure() — Rule 2",
        pill("ok", "付与") if trace.disclosure_appended else pill("skip", "対象外"),
        "" if trace.disclosure_appended
        else "定型文には付けません（危機対応は決定事項）",
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
                    live: bool = False) -> FastAPI:
    config = config or Config.from_env({"FREE_TIER_LIMIT": "3"})
    storage = storage or Storage(config.data_dir / "demo")

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

    warning = startup_warning()
    if warning:
        logger.warning(warning)

    @app.get("/", response_class=HTMLResponse)
    def index(preset: int = 0):
        _label, birth_date, birth_time, question = PRESETS[
            preset if 0 <= preset < len(PRESETS) else 0]
        return page(header() + banner() +
                    '<div class="cols">' +
                    form(birth_date, birth_time, question, "free", live) +
                    '<div>' + intro() + '</div>'
                    '</div>' + footer(config, storage))

    @app.post("/reading", response_class=HTMLResponse)
    async def reading(request: Request):
        # Parsed here rather than with FastAPI's Form(), which needs
        # python-multipart — not a dependency worth adding for a dev tool.
        # A GET form would avoid both, but it would put the user's question
        # in the URL and therefore in the server log and browser history,
        # and that question can be 死にたい.
        fields = _parse_form(await request.body())
        birth_date = fields.get("birth_date", "")
        birth_time = fields.get("birth_time", "")
        question = fields.get("question", "")
        tier = fields.get("tier", "free")

        birth = _birth_from_form(birth_date, birth_time)
        trace = ReadingTrace()

        # The real pipeline. Nothing about this call is demo-specific.
        outcome = service.generate(DEMO_USER, question, birth=birth,
                                   tier=tier, trace=trace)

        # Sent through a real Transport, then rendered from what the
        # transport received — so this page cannot show anything that was
        # not cleared to send.
        transport = NullTransport()
        transport.reply("demo-reply-token", outcome.message)
        delivered = transport.sent[-1]["message"]

        body = [header(), banner(), '<div class="cols">',
                form(birth_date, birth_time, question, tier, live), '<div>',
                reply_card(delivered, question),
                pipeline_card(trace, outcome.outcome, outcome.cost_usd,
                              outcome.review_id)]
        if trace.chart:
            body.append(chart_card(trace.chart))
        body.append('</div></div>')
        body.append(footer(config, storage))
        return page("".join(body))

    @app.get("/reset")
    def reset():
        storage.upsert_user(DEMO_USER, {"quota_reset_date": "1970-01-01",
                                        "free_quota_used": 0})
        return RedirectResponse("/", status_code=303)

    return app


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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8100)
    parser.add_argument("--live", action="store_true",
                        help="call the real model instead of the stub. "
                             "Needs OPENAI_API_KEY; still budget-guarded.")
    args = parser.parse_args()

    import uvicorn

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    if args.live:
        load_env()
    config = Config.from_env() if args.live else Config.from_env(
        {"FREE_TIER_LIMIT": "3"})

    print(f"\n  AI Uranai demo — http://127.0.0.1:{args.port}")
    print(f"  model: {'LIVE (billed, budget-guarded)' if args.live else 'stub (no network, no spend)'}\n")

    uvicorn.run(create_demo_app(config, live=args.live),
                host="127.0.0.1", port=args.port)


if __name__ == "__main__":  # pragma: no cover
    main()

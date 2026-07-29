"""
Japanese prompts — ALL PLACEHOLDERS.

NOTHING IN THIS FILE IS PRODUCTION COPY.
========================================
Every prompt here is scaffolding to prove the pipeline works. The real ones
are written by the retained practitioner, in Japanese, from scratch. This is
the single most important thing to remember about this module, which is why
`PROMPTS_ARE_PLACEHOLDERS` is checked at startup and logged as a warning
every time the bot boots, and why `bot/tests/test_prompts.py` fails if a
prompt is added without the marker.

Translated copy is precisely what a Japanese user rejects, and preventing
that is a large part of what a practitioner is being paid for. See P5 in
docs/DECISIONS.md.

WHAT WAS CARRIED FROM THE PROTOTYPE, AND WHAT WAS NOT
-----------------------------------------------------
Sprint 01 says to keep the prototype's constraint list, and most of it is
close to what compliance requires. Three things changed on the way in.

1. The persona. The prototype's system prompt claimed fluency in
   占星術・数秘術・タロット・四柱推命・九星気学. Multi-system synthesis is
   on the CLAUDE.md do-not-build list, and a model told it knows five
   systems will improvise the four we do not compute. This reader knows
   四柱推命, because that is the one the engine actually produces.

2. Crisis routing. The prototype instructed the model to direct serious
   distress to a helpline. That is `screen_input`'s job, deterministically,
   before the model is reached at all. The instruction is kept below as
   defence in depth and is explicitly NOT the enforcement point — if it
   ever fires, something upstream has already failed.

3. 「個人の感想です」 was dropped. It is not AI disclosure and it reads like
   a substitute for one. Rule 2 disclosure is appended by
   `outbound.reading()`, outside the model's control, where it cannot be
   forgotten, reworded or omitted by a generation. A model emitting its own
   disclaimer means unreviewed user-facing copy.

THE MODEL DOES NOT CALCULATE
----------------------------
The chart arrives already computed. The prompts below say so in as many
words, but the guarantee is structural, not textual: the model is never
given a birth date to work from, only a finished chart.
"""

from __future__ import annotations

from typing import Optional

# Checked at startup. Flip this when the practitioner's prompts land, and
# not before — it is the difference between a pilot and a product.
PROMPTS_ARE_PLACEHOLDERS = True

PLACEHOLDER_MARK = "# PLACEHOLDER — practitioner to rewrite. Do not ship."


# PLACEHOLDER — practitioner to rewrite. Do not ship.
SYSTEM_PROMPT = """あなたは四柱推命を専門とする占い師です。

【前提】
命式（年柱・月柱・日柱・時柱、日主、五行のバランス）は、すでに計算された
ものとして与えられます。あなたは計算をしません。
干支・節気・日付の計算をやり直したり、推測で補ったりしないでください。
与えられた命式だけを読み解いてください。

【表現の制約】
1. 「必ず」「絶対に」「100%」「確実に」などの断定は使わない
2. 「〜の傾向があります」「〜かもしれません」「〜と言われています」と述べる
3. 医療・健康・法律・投資に関する助言はしない
4. 相手を不安にさせる表現、脅すような表現は使わない
5. 絵文字は使わないか、ごく控えめに
6. ですます調で、落ち着いた、やわらかい語り口で
7. 300〜500字程度

【4 について補足】
不安をあおって何かを勧める形は、この商品では禁止です。
出力は自動で検査され、この形に該当した場合は送信されません。

【3 について補足】
深刻な悩みへの対応は、この応答より前の段階で処理されています。
ここに届いている時点で、その判定は済んでいます。
"""


# PLACEHOLDER — practitioner to rewrite. Do not ship.
READING_PROMPT = """以下の命式をもとに、ご相談へのお返事を書いてください。

{chart}
{hour_note}
【ご相談】
{question}

【書き方】
- 命式から読み取れる傾向を、ご相談に引きつけて述べてください
- 断定を避け、可能性として述べてください
- 最後に、今日から試せる小さなことを一つだけ添えてください
"""


# PLACEHOLDER — practitioner to rewrite. Do not ship.
DAILY_PROMPT = """以下の命式をもとに、{today} の過ごし方について書いてください。

{chart}
{hour_note}
【書き方】
- 命式から読み取れる、その日の傾向を述べてください
- 断定を避け、可能性として述べてください
- 最後に、今日から試せる小さなことを一つだけ添えてください
"""


# Inserted when the birth time was not given. P6: the engine refuses to
# invent a 時柱, and this is what stops the model quietly filling the gap
# the engine deliberately left.
#
# PLACEHOLDER — practitioner to rewrite. Do not ship.
HOUR_UNKNOWN_NOTE = """
【重要】出生時刻の申告がないため、時柱はありません。三柱で拝見します。
時柱から読み取る事柄（時刻に由来する性質、晩年の傾向、子どもに関することなど）
には触れないでください。わからないものは、わからないままにしてください。
「時刻がわかれば、さらに詳しく見られます」と一言添えるのは構いません。
"""

# PLACEHOLDER — practitioner to rewrite. Do not ship.
HOUR_KNOWN_NOTE = ""


def hour_note(hour_known: bool) -> str:
    return HOUR_KNOWN_NOTE if hour_known else HOUR_UNKNOWN_NOTE


def build_reading_prompt(chart_text: str, question: str,
                         hour_known: bool = True) -> str:
    """Assemble the user-side prompt for a question-led reading.

    Returns a string. Turning it into something a model will accept is
    `reading.py`'s job, and requires a screening token.
    """
    return READING_PROMPT.format(
        chart=chart_text,
        hour_note=hour_note(hour_known),
        question=question,
    )


def build_daily_prompt(chart_text: str, today: str,
                       hour_known: bool = True) -> str:
    return DAILY_PROMPT.format(
        chart=chart_text,
        hour_note=hour_note(hour_known),
        today=today,
    )


def startup_warning() -> Optional[str]:
    """Returned by config validation at boot and logged at WARNING.

    A placeholder prompt that nobody notices is how scaffolding ends up in
    front of a paying user.
    """
    if not PROMPTS_ARE_PLACEHOLDERS:
        return None
    return (
        "PROMPTS ARE PLACEHOLDERS. bot/prompts_ja.py has not been written by "
        "the retained practitioner. Friends-and-family testing only — see "
        "CLAUDE.md, 'Before anything reaches a real user'."
    )

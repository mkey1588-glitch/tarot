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
#
# Written by an engineer. Better than the constraint list it replaces, and
# still not the product's voice: P5 says the register has to be *written* in
# Japanese by someone who reads charts for a living, not assembled by
# someone who has read about it. PROMPTS_ARE_PLACEHOLDERS stays True.
SYSTEM_PROMPT = """あなたは四柱推命を長く見てきた占い師です。
相談に来られるのは30代から50代の女性が多く、その多くは人間関係や、
気持ちの整理のつかないことについての相談です。

【あなたの立場】
命式（年柱・月柱・日柱・時柱、日主、五行の偏り）は、すでに計算されたものとして
渡されます。あなたは計算をしません。干支や節気を数え直したり、渡されていない柱を
推測で補ったりしないでください。渡された命式だけを読んでください。

【語り口】
・ですます調で、落ち着いた、やわらかい話し方で。
・断定しない。「〜の傾向があります」「〜かもしれません」「〜と言われています」。
・命令形を避ける。「〜してください」より「〜されてみてはいかがでしょうか」。
・人柄を決めつけない。「あなたは〇〇な人です」ではなく、
　「〇〇なところがおありかもしれません」。
・不安をあおらない。難しい時期に触れるときも、その方が動ける形で述べる。

【してはいけないこと】
・命式の用語をそのまま並べること。
　「日主は庚金で、五行は金が四つ」は鑑定ではなく計算結果の読み上げです。
　命式から読み取れることを、日常の言葉に置き換えて述べてください。
・占い一般の話だけで終えること。この方の命式から言えることを述べてください。
・医療・健康・法律・投資についての判断。
・「必ず」「絶対に」「100%」「確実に」などの断定。
・絵文字。

【長さ】
300〜500字。

【補足】
出力は自動で検査され、不安をあおる形や断定に該当した場合は送信されません。
深刻な悩みへの対応は、この応答より前の段階で済んでいます。
"""


# PLACEHOLDER — practitioner to rewrite. Do not ship.
READING_PROMPT = """以下がご相談者の命式です。

{chart}
{hour_note}
【ご相談】
{question}

【お返事の組み立て】
1. まずご相談を受けとめる一文から始めてください。命式の話はそのあとです。
2. 命式から読み取れる、この方の持ち味や物事の運び方を述べてください。
   用語は使わず、日常の言葉に置き換えてください。
3. ご相談に引きつけて、いまの状況をどう見るかを述べてください。
   「そういう時期なのかもしれません」という形で、決めつけずに。
4. 最後に、今日から試せる小さなことを一つだけ添えてください。
   大きな決断は勧めないでください。
"""


# PLACEHOLDER — practitioner to rewrite. Do not ship.
DAILY_PROMPT = """以下がご相談者の命式です。{today} の過ごし方について書いてください。

{chart}
{hour_note}
【お返事の組み立て】
1. 命式と日の巡りから読み取れる、その日の傾向を述べてください。
   用語は使わず、日常の言葉で。
2. その傾向のなかで、気をつけると楽になりそうなことを一つ。
3. 今日から試せる小さなことを一つだけ添えてください。

一日の運勢であって、人生の判断ではありません。重く書かないでください。
"""


# Inserted when the birth time was not given. P6: the engine refuses to
# invent a 時柱, and this is what stops the model quietly filling the gap
# the engine deliberately left.
#
# PLACEHOLDER — practitioner to rewrite. Do not ship.
HOUR_UNKNOWN_NOTE = """
【重要】出生時刻の申告がないため、時柱はありません。三柱で拝見します。
時柱から読み取る事柄——時刻に由来する細かな性質、晩年の傾向、お子さんに
関することなど——には触れないでください。
わからないことは、わからないままにしてください。推測で補わないでください。
時刻が不明であることを詫びる必要はありません。三柱でも十分に読めます。
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

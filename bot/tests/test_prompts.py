"""Tests for the placeholder prompts.

Not tests of prompt quality — there is nothing to assess yet, and assessing
it is the practitioner's job. These check the two things that can go wrong
while we wait: a prompt losing its marker and quietly becoming load-bearing,
and a prompt asking the model to do something the engine exists to prevent.
"""

import ast
from pathlib import Path

import pytest

from bot import prompts_ja
from bot.prompts_ja import (
    DAILY_PROMPT, HOUR_UNKNOWN_NOTE, PLACEHOLDER_MARK,
    PROMPTS_ARE_PLACEHOLDERS, READING_PROMPT, SYSTEM_PROMPT,
    build_daily_prompt, build_reading_prompt, startup_warning,
)

SOURCE = Path(prompts_ja.__file__).read_text(encoding="utf-8")


def prompt_constants():
    """Module-level string constants that are prompt text.

    Read from source so a newly added prompt is picked up automatically —
    which is the point, since the risk is an unmarked prompt nobody noticed.
    """
    tree = ast.parse(SOURCE)
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not isinstance(node.value, ast.Constant) or \
                not isinstance(node.value.value, str):
            continue
        name = node.targets[0].id
        if name in {"PLACEHOLDER_MARK"}:
            continue
        yield name, node.lineno, node.value.value


# --- Every prompt is unmistakably marked ----------------------------------

def test_prompt_constants_were_found():
    names = {name for name, _, _ in prompt_constants()}
    assert {"SYSTEM_PROMPT", "READING_PROMPT", "DAILY_PROMPT"} <= names


@pytest.mark.parametrize("name,lineno,_text",
                         list(prompt_constants()),
                         ids=lambda v: v if isinstance(v, str) else "")
def test_every_prompt_carries_the_placeholder_marker(name, lineno, _text):
    """We are paying a practitioner to write these. Losing track of which
    strings are scaffolding is how scaffolding ships."""
    lines = SOURCE.splitlines()
    preceding = "\n".join(lines[max(0, lineno - 8):lineno - 1])
    assert PLACEHOLDER_MARK in preceding, (
        f"{name} (line {lineno}) has no '{PLACEHOLDER_MARK}' above it"
    )


def test_the_placeholder_flag_is_still_set():
    """Flipped only when the practitioner's prompts land. If this test is
    failing because someone flipped it, the prompts had better be theirs."""
    assert PROMPTS_ARE_PLACEHOLDERS is True


def test_startup_emits_a_warning_while_prompts_are_placeholders():
    warning = startup_warning()
    assert warning and "PLACEHOLDER" in warning


# --- The prompts do not ask the model to calculate ------------------------

def test_the_system_prompt_forbids_recomputing_the_chart():
    """E1. A hallucinated chart is the one error a knowledgeable user spots
    instantly, and the prompt should not be the only thing preventing it —
    but it should not invite it either."""
    assert "計算をしません" in SYSTEM_PROMPT
    assert "推測で補ったり" in SYSTEM_PROMPT


def test_no_prompt_asks_the_model_for_a_birth_date():
    """The model receives a finished chart. If a prompt asks it to work from
    a birthday, the engine has been bypassed."""
    for name, _, text in prompt_constants():
        assert "誕生日" not in text, f"{name} asks the model about a birthday"
        assert "生年月日から" not in text, f"{name} asks the model to derive"


def test_the_persona_is_four_pillars_only():
    """The prototype claimed five systems. A model told it knows five will
    improvise the four we do not compute, and multi-system synthesis is on
    the do-not-build list."""
    assert "四柱推命" in SYSTEM_PROMPT
    for other in ("占星術", "数秘術", "タロット", "九星気学", "星座"):
        assert other not in SYSTEM_PROMPT, f"{other} is not what we compute"


# --- Constraints that compliance depends on -------------------------------

def test_the_system_prompt_still_forbids_absolutes():
    for word in ("必ず", "絶対に", "100%", "確実に"):
        assert word in SYSTEM_PROMPT


def test_the_system_prompt_forbids_fear_framing():
    """Rule 1. The filter blocks it; the prompt should not produce it."""
    assert "不安" in SYSTEM_PROMPT


def test_the_prompt_does_not_claim_to_be_the_crisis_mechanism():
    """Crisis routing is screen_input's, before the model is reached. The
    prompt saying otherwise would be a lie about where the guarantee lives."""
    assert "前の段階で済んでいます" in SYSTEM_PROMPT


def test_the_model_is_not_asked_to_write_its_own_disclaimer():
    """「個人の感想です」 was dropped: it is not AI disclosure, and a model
    emitting its own disclaimer is unreviewed user-facing copy. Disclosure
    is appended by outbound.reading(), outside the model's control."""
    for name, _, text in prompt_constants():
        assert "個人の感想" not in text, f"{name} asks the model to disclaim"


# --- The unknown-hour constraint (P6) -------------------------------------

def test_an_unknown_hour_tells_the_model_to_stay_quiet():
    prompt = build_reading_prompt("【命式】...", "恋愛運を教えてください",
                                  hour_known=False)
    assert HOUR_UNKNOWN_NOTE.strip() in prompt
    assert "時柱はありません" in prompt


def test_a_known_hour_adds_no_such_note():
    prompt = build_reading_prompt("【命式】...", "恋愛運を教えてください",
                                  hour_known=True)
    assert "時柱はありません" not in prompt


def test_the_unknown_hour_note_names_what_not_to_read():
    """"Be vague" is not an instruction. It has to say which topics."""
    assert "触れないでください" in HOUR_UNKNOWN_NOTE
    assert "わからないままにして" in HOUR_UNKNOWN_NOTE


# --- Assembly --------------------------------------------------------------

def test_the_reading_prompt_carries_the_chart_and_the_question():
    prompt = build_reading_prompt("【命式】年柱 庚午", "転職を考えています")
    assert "【命式】年柱 庚午" in prompt
    assert "転職を考えています" in prompt


def test_the_daily_prompt_carries_the_chart_and_the_date():
    prompt = build_daily_prompt("【命式】年柱 庚午", "2026-07-30")
    assert "【命式】年柱 庚午" in prompt
    assert "2026-07-30" in prompt


def test_assembled_prompts_leave_no_unfilled_placeholders():
    for prompt in (build_reading_prompt("chart", "question", True),
                   build_reading_prompt("chart", "question", False),
                   build_daily_prompt("chart", "2026-07-30", True),
                   build_daily_prompt("chart", "2026-07-30", False)):
        assert "{" not in prompt and "}" not in prompt


# --- The failure mode a chart-fed model actually has ----------------------

def test_the_prompt_forbids_reciting_the_chart_back():
    """A model handed 庚午 辛巳 庚辰 will list it. A practitioner does not
    say "your day master is 庚金" to a client — they say what it means. This
    is the single most likely way a technically correct reading still reads
    like a machine."""
    assert "読み上げ" in SYSTEM_PROMPT
    assert "日常の言葉に置き換えて" in SYSTEM_PROMPT


def test_the_prompt_forbids_the_imperative():
    """「〜してください」 is the register of an instruction manual. The
    target user is being spoken to, not configured."""
    assert "命令形" in SYSTEM_PROMPT


def test_the_prompt_forbids_pronouncing_on_character():
    """「あなたは〇〇な人です」 is the tone that makes fortune-telling feel
    like judgement rather than company."""
    assert "決めつけない" in SYSTEM_PROMPT


def test_the_reading_is_told_to_answer_before_it_explains():
    """Opening with the chart makes the reply about our machinery. The
    person asked a question."""
    assert "ご相談を受けとめる一文から始めて" in READING_PROMPT
    assert "命式の話はそのあと" in READING_PROMPT


def test_the_reading_ends_with_something_small():
    """Rule 3 territory: a reading that recommends a big decision is giving
    consequential advice."""
    assert "小さなこと" in READING_PROMPT
    assert "大きな決断は勧めないで" in READING_PROMPT


def test_an_unknown_hour_is_not_apologised_for():
    """Most people do not know their birth time. Treating that as a
    deficiency is how a product tells a user they came unprepared."""
    assert "詫びる必要はありません" in HOUR_UNKNOWN_NOTE
    assert "推測で補わないで" in HOUR_UNKNOWN_NOTE


def test_the_placeholder_flag_survived_the_rewrite():
    """Better copy is not practitioner copy. Gate 2 stays open, and payment
    stays locked behind it."""
    assert PROMPTS_ARE_PLACEHOLDERS is True

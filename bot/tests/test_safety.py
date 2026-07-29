"""Tests for the compliance filter.

These encode the five non-negotiable rules from Section 12 of the board
package. A failure here is not a style problem — it is the shape of conduct
that Japanese consumer regulation targets and that would be a company-ending
reputational risk.
"""

import pytest

from bot.safety import (
    Verdict, screen_input, screen_output, with_disclosure,
    AI_DISCLOSURE_SHORT,
)


# --- Rule 3: crisis never reaches the model --------------------------------

@pytest.mark.parametrize("text", [
    "もう死にたいです",
    "消えたい気持ちが止まらない",
    "自殺を考えています",
    "生きていたくない",
])
def test_crisis_language_is_redirected_not_read(text):
    result = screen_input(text)
    assert result.verdict is Verdict.REDIRECT_CRISIS
    assert not result.allowed
    assert result.reply and "0570-064-556" in result.reply


def test_crisis_reply_offers_help_rather_than_a_reading():
    reply = screen_input("死にたい").reply
    assert "占い" not in reply or "占いではなく" in reply


def test_crisis_check_runs_before_professional_check():
    """A message that trips both must route to crisis, not to a doctor."""
    result = screen_input("うつ病で死にたい")
    assert result.verdict is Verdict.REDIRECT_CRISIS


# --- Rule 3: professional domains ------------------------------------------

@pytest.mark.parametrize("text,expected_fragment", [
    ("癌が治るか占ってください", "医療機関"),
    ("裁判に勝てますか", "弁護士"),
    ("この株を買うべきでしょうか", "ファイナンシャル"),
])
def test_professional_domains_are_redirected(text, expected_fragment):
    result = screen_input(text)
    assert result.verdict is Verdict.REDIRECT_PROFESSIONAL
    assert expected_fragment in result.reply


def test_ordinary_questions_are_allowed():
    for text in ["恋愛運を教えて", "今日の運勢は？", "転職を考えています",
                 "相性を見てほしい"]:
        assert screen_input(text).allowed, text


# --- 景品表示法: no claims of certainty ------------------------------------

@pytest.mark.parametrize("text", [
    "必ず良い方向に向かいます",
    "絶対に成功します",
    "100%当たります",
    "確実に良くなるでしょう",
])
def test_absolute_claims_are_blocked(text):
    result = screen_output(text)
    assert result.verdict is Verdict.BLOCK
    assert "景品表示法" in result.reason


def test_hedged_language_passes():
    for text in ["良い方向に向かう傾向があります",
                 "変化が訪れるかもしれません",
                 "と言われています"]:
        assert screen_output(text).allowed, text


# --- Rule 1: never monetise fear -------------------------------------------

def test_fear_plus_remedy_is_blocked():
    """This exact shape — misfortune coming, payment averts it — is what the
    amended 消費者契約法 makes voidable."""
    result = screen_output("このままでは災いが訪れます。お祓いを申し込みください。")
    assert result.verdict is Verdict.BLOCK
    assert "霊感商法" in result.reason


def test_fear_alone_is_also_blocked():
    """Off-brand even without an upsell attached."""
    assert screen_output("あなたには因縁があります").verdict is Verdict.BLOCK


def test_a_paid_upsell_without_fear_is_not_blocked_here():
    """Selling is fine. Selling on fear is not. This filter must not become
    a blanket ban on mentioning the paid tier."""
    assert screen_output("より詳しい鑑定は有料でご案内しています").allowed


# --- Rule 2: disclosure ----------------------------------------------------

def test_disclosure_is_appended():
    out = with_disclosure("今日は穏やかな一日になりそうです")
    assert out.endswith(AI_DISCLOSURE_SHORT)
    assert "AI" in out

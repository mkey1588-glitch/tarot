"""Tests for the bridge between the engine and the interpretation layer.

This is the only place chart data is allowed to reach a prompt, so what it
does and does not put in that prompt is worth pinning down.
"""

from datetime import datetime, timedelta, timezone

import pytest

from bot.chart_service import ManualReviewRequired, build_payload, format_for_prompt
from engine.solar import lichun, solar_term_instant

JST = timezone(timedelta(hours=9))


# --- What reaches the prompt ----------------------------------------------

def test_the_prompt_carries_every_computed_value():
    text = format_for_prompt(build_payload(datetime(1990, 5, 15, 7, 30)))
    assert "年柱 庚午" in text
    assert "月柱 辛巳" in text
    assert "日柱 庚辰" in text
    assert "時柱 庚辰" in text
    assert "節気: 立夏" in text
    assert "【日主】庚" in text
    assert "【五行】" in text


def test_the_prompt_does_not_carry_the_birth_datetime():
    """A model handed a birth date has the raw material to recompute a
    pillar, which is the behaviour E1 exists to prevent — and it is personal
    information going to a third party for no interpretive benefit."""
    payload = build_payload(datetime(1990, 5, 15, 7, 30))
    text = format_for_prompt(payload)

    assert payload["birth_local"] == "1990-05-15T07:30:00+09:00"
    for fragment in ("1990", "05-15", "07:30", "1990-05-15"):
        assert fragment not in text, f"{fragment!r} reached the prompt"


def test_an_unknown_hour_is_stated_rather_than_omitted():
    """Silence would leave a reviewer wondering whether the pillar was lost
    between the engine and the prompt."""
    text = format_for_prompt(build_payload(datetime(1990, 5, 15),
                                           hour_known=False))
    assert "時柱 不明" in text
    assert "三柱で鑑定" in text


def test_a_known_hour_is_rendered_as_a_pillar():
    text = format_for_prompt(build_payload(datetime(1990, 5, 15, 7, 30)))
    assert "時柱 庚辰" in text
    assert "不明" not in text


def test_the_payload_marks_whether_the_hour_is_known():
    assert build_payload(datetime(1990, 5, 15, 7, 30))["hour_known"] is True
    assert build_payload(datetime(1990, 5, 15),
                         hour_known=False)["hour_known"] is False


# --- Manual review ---------------------------------------------------------

def test_a_boundary_chart_raises_rather_than_answering():
    instant = lichun(2001).astimezone(JST)
    with pytest.raises(ManualReviewRequired) as exc:
        build_payload(instant + timedelta(minutes=5))
    assert exc.value.warnings
    assert "立春" in str(exc.value)


def test_an_unknown_hour_on_a_term_day_raises():
    """P6: with no clock time the whole civil day is uncertain, so a term
    falling on the birth date leaves the month pillar genuinely ambiguous."""
    instant = solar_term_instant(135.0, datetime(2020, 8, 7, tzinfo=timezone.utc))
    with pytest.raises(ManualReviewRequired):
        build_payload(instant.astimezone(JST).replace(hour=0, minute=0),
                      hour_known=False)


def test_an_uncertain_chart_can_be_computed_deliberately():
    """For the reviewer's own tooling, never for an automatic answer."""
    instant = lichun(2001).astimezone(JST)
    payload = build_payload(instant + timedelta(minutes=5), allow_uncertain=True)
    assert payload["needs_manual_review"] is True
    assert payload["boundary_warnings"]


def test_an_ordinary_chart_does_not_raise():
    assert build_payload(datetime(1990, 5, 15, 7, 30))["needs_manual_review"] is False

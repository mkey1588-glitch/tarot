"""Tests for the reply choke point.

The invariant: nothing reaches a user that has not been screened, and no
generated reading reaches a user without the AI disclosure.

The structural tests at the bottom are the ones that matter most. A single
funnel only holds if replies are the only way text reaches a user — and the
daily reading will be a push, not a reply. Those tests are written to fail
on the day that path is added without going through here.
"""

import inspect

import pytest

from bot import outbound
from bot.messages_ja import NEVER_APPEND, TEMPLATES, Msg
from bot.outbound import (
    LINE_MAX_CHARS, NullTransport, Outbound, Transport, UnscreenedOutput,
    canned, reading,
)
from bot.safety import AI_DISCLOSURE_SHORT, Verdict, screen_output


# --- Minting ---------------------------------------------------------------

def test_outbound_cannot_be_constructed_directly():
    with pytest.raises(UnscreenedOutput):
        Outbound(text="なんでも送れてしまう", kind="reading")


def test_canned_refuses_a_bare_string():
    """There is deliberately no send_text(str): it is one refactor away from
    being handed a fragment of model output."""
    with pytest.raises(UnscreenedOutput, match="Msg member"):
        canned("こんにちは")


def test_there_is_no_helper_that_takes_free_text():
    """Exactly two ways to make an Outbound. A third that took a bare string
    would make both guarantees optional."""
    public_functions = {
        name for name, obj in vars(outbound).items()
        if inspect.isfunction(obj) and not name.startswith("_")
        and obj.__module__ == "bot.outbound"
    }
    assert public_functions == {"canned", "reading"}


# --- Readings: screened, then disclosed ------------------------------------

def test_a_clean_reading_is_disclosed():
    out = reading("穏やかな時期に向かう傾向があります")
    assert out.kind == "reading"
    assert out.text.endswith(AI_DISCLOSURE_SHORT)
    assert "穏やかな時期" in out.text


def test_every_reading_carries_the_disclosure():
    """Rule 2 is not conditional on the content of the reading."""
    for text in ["短い", "傾向があります", "変化が訪れるかもしれません" * 50]:
        assert reading(text).text.endswith(AI_DISCLOSURE_SHORT)


def test_a_blocked_reading_never_reaches_the_user():
    out = reading("必ず良い方向に向かいます")
    assert "必ず良い方向に向かいます" not in out.text
    assert out.text == TEMPLATES[Msg.READING_UNAVAILABLE]


def test_a_blocked_reading_records_why_for_the_weekly_review():
    """A block is a prompt defect (E5). The reason goes to the practitioner,
    not to the user."""
    out = reading("このままでは災いが訪れます。お祓いを申し込みください。")
    assert out.blocked_reason and "霊感商法" in out.blocked_reason
    assert "霊感商法" not in out.text
    assert "お祓い" not in out.text


def test_a_blocked_reading_is_not_given_the_disclosure():
    """The fallback is our copy, not a reading. Labelling it as an
    AI-generated 鑑定 would be describing something we did not send."""
    out = reading("絶対に成功します")
    assert not out.text.endswith(AI_DISCLOSURE_SHORT)
    assert out.kind == "canned"


def test_screening_happens_before_disclosure_is_appended():
    """Appending first would mean screening our own disclosure text, and a
    block would discard it along with the reading."""
    out = reading("良い傾向があります")
    assert out.text.index("良い傾向") < out.text.index(AI_DISCLOSURE_SHORT)


# --- Canned copy -----------------------------------------------------------

def test_canned_copy_is_returned_verbatim():
    assert canned(Msg.READING_UNAVAILABLE).text == \
           TEMPLATES[Msg.READING_UNAVAILABLE]


def test_canned_templates_take_parameters():
    assert "3" in canned(Msg.WELCOME, limit=3).text


def test_every_registered_message_has_a_template():
    assert set(Msg) == set(TEMPLATES)


def test_every_canned_message_passes_the_outbound_screen():
    """Screened here rather than at runtime, on purpose. Running the filter
    over our own copy at runtime would let an edit to the crisis message
    cause that message to be blocked — the worst failure this system has.
    Failing the build instead is strictly better."""
    for message, template in TEMPLATES.items():
        verdict = screen_output(template)
        assert verdict.verdict is Verdict.ALLOW, \
            f"{message.name} would be blocked: {verdict.reason}"


def test_canned_placeholders_are_all_named():
    """canned() passes keyword arguments only, so a positional {} in a
    template raises IndexError the first time that message is sent — which,
    for something like MANUAL_REVIEW, would be in front of a user."""
    from string import Formatter

    for message, template in TEMPLATES.items():
        fields = [name for _, name, _, _ in Formatter().parse(template)
                  if name is not None]
        assert all(field and not field.isdigit() for field in fields), \
            f"{message.name} has a positional placeholder: {fields}"


def test_every_parameterised_message_renders_with_its_arguments():
    """Each template that takes parameters is rendered somewhere, so a
    renamed placeholder fails here rather than at a user."""
    rendered = {
        Msg.WELCOME: canned(Msg.WELCOME, limit=3),
        Msg.HELP: canned(Msg.HELP, limit=3),
        Msg.BIRTH_DATA_SAVED: canned(Msg.BIRTH_DATA_SAVED,
                                     birth_summary="1990-05-15",
                                     time_note="時刻は不明として承ります。"),
        Msg.MANUAL_REVIEW: canned(Msg.MANUAL_REVIEW, review_id="abc123"),
        Msg.PAYWALL_OFFER: canned(Msg.PAYWALL_OFFER, price=300),
        Msg.OPERATOR_REVIEW_ALERT: canned(Msg.OPERATOR_REVIEW_ALERT,
                                          review_id="abc123"),
        Msg.CHECKOUT_HANDOFF: canned(Msg.CHECKOUT_HANDOFF, price=300,
                                     url="https://example.invalid/c/1"),
    }
    for message, out in rendered.items():
        assert "{" not in out.text and "}" not in out.text, message.name

    from string import Formatter
    parameterised = {m for m, t in TEMPLATES.items()
                     if any(n is not None for _, n, _, _ in Formatter().parse(t))}
    assert parameterised == set(rendered), (
        "a parameterised message is not exercised here: "
        f"{parameterised ^ set(rendered)}"
    )


def test_the_crisis_reply_carries_no_ai_disclosure():
    """Ruled explicitly. Someone who has just typed 死にたい should not then
    read a paragraph about automated screening: it makes a message that
    needs to be warm feel procedural, and 'we detected this' reads as
    surveillance. Disclosure belongs in onboarding and the privacy policy."""
    assert Msg.CRISIS in NEVER_APPEND
    text = canned(Msg.CRISIS).text
    assert AI_DISCLOSURE_SHORT not in text
    assert "AI" not in text


def test_the_crisis_reply_still_leads_with_helplines():
    text = canned(Msg.CRISIS).text
    assert "0570-064-556" in text
    assert "0120-279-338" in text


def test_onboarding_carries_the_full_ai_disclosure():
    """The half of Rule 2 that the per-reading footer does not cover."""
    for message in (Msg.WELCOME, Msg.HELP):
        assert "AI" in canned(message, limit=3).text


def test_professional_referrals_are_not_appended_to_either():
    for message in (Msg.PROFESSIONAL_MEDICAL, Msg.PROFESSIONAL_LEGAL,
                    Msg.PROFESSIONAL_FINANCIAL):
        assert message in NEVER_APPEND


# --- Chunking --------------------------------------------------------------

def test_long_readings_are_split_for_the_transport():
    out = reading("あ" * (LINE_MAX_CHARS * 2))
    chunks = out.chunks()
    assert len(chunks) == 3
    assert all(len(c) <= LINE_MAX_CHARS for c in chunks)
    assert "".join(chunks) == out.text


def test_a_short_reading_is_a_single_chunk():
    assert len(reading("短い").chunks()) == 1


# --- Transports: every sender, not just reply ------------------------------

def _import_every_bot_module():
    """__subclasses__ only knows about classes that have been imported.

    Without this the scan below would depend on pytest's collection order —
    it would happen to work today because test_app imports bot.app, and
    silently stop covering LineTransport the day that test is renamed.
    """
    import importlib
    import pkgutil

    import bot

    for module in pkgutil.iter_modules(bot.__path__):
        if module.name != "tests":
            importlib.import_module(f"bot.{module.name}")


def all_transport_classes():
    _import_every_bot_module()
    seen, stack = [], [Transport]
    while stack:
        for sub in stack.pop().__subclasses__():
            if sub not in seen:
                seen.append(sub)
                stack.append(sub)
    return seen


def test_the_scan_found_every_transport_in_the_package():
    """A scan that quietly matched nothing would make the tests below pass
    while checking nothing at all."""
    names = {cls.__name__ for cls in all_transport_classes()}
    assert {"NullTransport", "LineTransport"} <= names, names


@pytest.mark.parametrize("cls", all_transport_classes(),
                         ids=lambda c: c.__name__)
def test_every_public_transport_method_takes_an_outbound(cls):
    """The point of the whole module. A funnel on `reply` alone is not a
    guarantee: the daily reading is a push. Any sending method added to a
    transport — push, broadcast, multicast — must take an Outbound, and
    this fails the day one does not."""
    for name, method in inspect.getmembers(cls, inspect.isfunction):
        if name.startswith("_"):
            continue
        params = inspect.signature(method).parameters
        assert any(p.annotation in (Outbound, "Outbound")
                   for p in params.values()), (
            f"{cls.__name__}.{name} does not take an Outbound. Every path "
            f"that puts text in front of a user goes through outbound."
        )


@pytest.mark.parametrize("cls", all_transport_classes(),
                         ids=lambda c: c.__name__)
def test_every_public_transport_method_checks_at_runtime(cls):
    """The annotation check above is static and a lie is cheap. This one
    reads the body: every sender must actually call _require_outbound, so a
    transport constructed with credentials we do not have in tests is still
    covered."""
    for name, method in inspect.getmembers(cls, inspect.isfunction):
        if name.startswith("_") or method.__qualname__.startswith("Transport."):
            continue
        source = inspect.getsource(method)
        assert "_require_outbound" in source, (
            f"{cls.__name__}.{name} does not call _require_outbound"
        )


def test_a_transport_rejects_a_bare_string_at_runtime():
    transport = NullTransport()
    with pytest.raises(UnscreenedOutput):
        transport.reply("token", "生の文字列")
    with pytest.raises(UnscreenedOutput):
        transport.push("U1", "生の文字列")


def test_the_abstract_transport_declares_both_reply_and_push():
    """Push is declared up front precisely because it is not used yet. A
    path that does not exist cannot be forgotten if the base class already
    demands the right shape for it."""
    assert set(Transport.__abstractmethods__) == {"reply", "push"}


def test_null_transport_records_what_it_would_have_sent():
    transport = NullTransport()
    transport.reply("token", canned(Msg.HELP, limit=3))
    transport.push("U1", reading("穏やかな傾向があります"))
    assert [entry["via"] for entry in transport.sent] == ["reply", "push"]
    assert transport.texts[1].endswith(AI_DISCLOSURE_SHORT)

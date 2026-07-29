"""Tests for the model choke point.

The subject here is not "does the client work". It is whether an unscreened
message or an over-budget call can reach a model at all.
"""

import pytest

from bot.cost import BudgetExceeded, BudgetGuard
from bot.llm import (
    Completion, ModelGateway, ScreenedPrompt, StubModel, MAX_OUTPUT_TOKENS,
)
from bot.safety import ScreeningToken, UnscreenedInput, Verdict, screen_input
from bot.storage import Storage


@pytest.fixture
def store(tmp_path):
    return Storage(tmp_path / "data")


@pytest.fixture
def gateway(store):
    return ModelGateway(StubModel(), BudgetGuard(store, budget_usd=10.0))


def a_screened_prompt(user="恋愛運を教えてください") -> ScreenedPrompt:
    result = screen_input(user)
    assert result.allowed
    return ScreenedPrompt(system="system", user=user, token=result.token)


# --- The screening gate ----------------------------------------------------

def test_screen_input_issues_a_token_when_it_allows():
    result = screen_input("恋愛運を教えてください")
    assert result.allowed
    assert isinstance(result.token, ScreeningToken)


def test_a_blocked_verdict_carries_no_token():
    """A caller who ignores the verdict still cannot build a prompt."""
    for text in ("死にたい", "癌は治りますか", "この株を買うべき"):
        result = screen_input(text)
        assert not result.allowed
        assert result.token is None


def test_a_token_cannot_be_minted_outside_safety():
    with pytest.raises(UnscreenedInput):
        ScreeningToken()
    with pytest.raises(UnscreenedInput):
        ScreeningToken("guess")


def test_a_prompt_cannot_be_built_without_a_token():
    with pytest.raises(UnscreenedInput):
        ScreenedPrompt(system="s", user="u", token=None)
    with pytest.raises(UnscreenedInput):
        ScreenedPrompt(system="s", user="u", token=object())


def test_a_crisis_message_cannot_be_turned_into_a_prompt():
    """The end-to-end shape of the rule: crisis language has no route to a
    model, because the thing a model call needs is never issued."""
    result = screen_input("もう死にたい")
    assert result.verdict is Verdict.REDIRECT_CRISIS
    with pytest.raises(UnscreenedInput):
        ScreenedPrompt(system="s", user="もう死にたい", token=result.token)


def test_the_gateway_refuses_a_bare_string(gateway):
    with pytest.raises(UnscreenedInput, match="ScreenedPrompt"):
        gateway.complete("恋愛運を教えてください", user_id="U1",
                         model="gpt-4o-mini")


def test_the_gateway_refuses_a_lookalike_object(gateway):
    class NotAPrompt:
        system = "s"
        user = "u"
        token = None

    with pytest.raises(UnscreenedInput):
        gateway.complete(NotAPrompt(), user_id="U1", model="gpt-4o-mini")


# --- The budget gate -------------------------------------------------------

def test_a_screened_prompt_completes(gateway):
    completion = gateway.complete(a_screened_prompt(), user_id="U1",
                                  model="gpt-4o-mini")
    assert completion.text
    assert completion.model == "gpt-4o-mini"
    assert completion.cost_usd > 0


def test_the_budget_is_checked_before_the_transport_is_touched(store):
    """If the guard refuses, the model must not have been called at all —
    otherwise the cap is a report on spending that already happened."""
    class ExplodingModel:
        called = False

        def generate(self, *args, **kwargs):
            ExplodingModel.called = True
            raise AssertionError("transport reached despite an exhausted budget")

    gateway = ModelGateway(ExplodingModel(), BudgetGuard(store, budget_usd=0.0))
    with pytest.raises(BudgetExceeded):
        gateway.complete(a_screened_prompt(), user_id="U1", model="gpt-4o-mini")
    assert not ExplodingModel.called


def test_screening_is_checked_before_the_budget(store):
    """An unscreened call is rejected on its own terms, not because the
    budget happened to be exhausted. Otherwise topping up the budget would
    quietly open a hole."""
    gateway = ModelGateway(StubModel(), BudgetGuard(store, budget_usd=0.0))
    with pytest.raises(UnscreenedInput):
        gateway.complete("bare string", user_id="U1", model="gpt-4o-mini")


def test_every_call_is_recorded_against_the_budget(gateway, store):
    for _ in range(3):
        gateway.complete(a_screened_prompt(), user_id="U1", model="gpt-4o-mini")
    records = list(store.iter_llm_usage())
    assert len(records) == 3
    assert all(r["cost_usd"] > 0 and r["tier"] == "free" for r in records)


def test_the_tier_is_recorded_so_paid_and_free_spend_can_be_told_apart(gateway,
                                                                      store):
    gateway.complete(a_screened_prompt(), user_id="U1", model="gpt-4o",
                     tier="paid")
    assert list(store.iter_llm_usage())[0]["tier"] == "paid"


def test_output_is_capped_by_default(gateway):
    """The cap is what makes the guard's worst case a finite number."""
    class RecordingModel:
        seen = {}

        def generate(self, prompt, *, model, max_output_tokens, temperature):
            RecordingModel.seen["max"] = max_output_tokens
            return Completion("ok", model, 10, 10)

    ModelGateway(RecordingModel(), gateway.guard).complete(
        a_screened_prompt(), user_id="U1", model="gpt-4o-mini")
    assert RecordingModel.seen["max"] == MAX_OUTPUT_TOKENS


# --- The stub --------------------------------------------------------------

def test_the_stub_needs_no_credentials_and_no_network(gateway):
    """Sprint 01 requires the whole path to run without either."""
    completion = gateway.complete(a_screened_prompt(), user_id="U1",
                                  model="gpt-4o-mini")
    assert "スタブ" in completion.text


def test_the_stub_reply_can_be_overridden_for_tests(store):
    gateway = ModelGateway(StubModel("差し替えた応答"),
                           BudgetGuard(store, budget_usd=1.0))
    completion = gateway.complete(a_screened_prompt(), user_id="U1",
                                  model="gpt-4o-mini")
    assert completion.text == "差し替えた応答"

"""Tests for the demo page.

A demo is worth showing someone only if it shows what actually happened, so
these check that it renders the real pipeline's output and that it cannot
become a second route to a model or a second route to a user.
"""

import pytest
from fastapi.testclient import TestClient

from bot.config import Config
from bot.cost import BudgetGuard
from bot.demo import DEMO_USER, PRESETS, create_demo_app
from bot.llm import ModelGateway, StubModel
from bot.reading import ReadingService
from bot.safety import AI_DISCLOSURE_SHORT
from bot.storage import Storage

FORM = {"Content-Type": "application/x-www-form-urlencoded"}


def bubbles(body: str):
    """The chat bubbles — i.e. everything the user would actually see.

    The rest of the page is the operator's view, which deliberately shows
    more, so "is it on the page" is the wrong question to ask of a reply.
    """
    import re
    return re.findall(r'<div class="bubble[^"]*">(.*?)</div>', body, re.S)


@pytest.fixture
def store(tmp_path):
    return Storage(tmp_path / "data")


@pytest.fixture
def config(tmp_path):
    return Config.from_env({"FREE_TIER_LIMIT": "3",
                            "DATA_DIR": str(tmp_path / "data")})


def make_client(store, config, model=None, budget=10.0):
    gateway = ModelGateway(model or StubModel(), BudgetGuard(store, budget))
    return TestClient(create_demo_app(
        config, store, ReadingService(store, gateway, config)))


@pytest.fixture
def client(store, config):
    return make_client(store, config)


def read(client, birth_date="1990-05-15", birth_time="07:30",
         question="恋愛運を教えてください", tier="free"):
    return client.post("/reading", headers=FORM, data={
        "birth_date": birth_date, "birth_time": birth_time,
        "question": question, "tier": tier,
    })


# --- It renders ------------------------------------------------------------

def test_the_page_loads(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "パイプライン" in response.text


@pytest.mark.parametrize("index", range(len(PRESETS)))
def test_every_preset_runs_without_error(client, index):
    response = client.get(f"/?preset={index}")
    assert response.status_code == 200
    _label, birth_date, birth_time, question = PRESETS[index]
    submitted = read(client, birth_date, birth_time, question)
    assert submitted.status_code == 200


def test_an_out_of_range_preset_falls_back_rather_than_500ing(client):
    assert client.get("/?preset=99").status_code == 200
    assert client.get("/?preset=-4").status_code == 200


# --- It shows what the pipeline actually did -------------------------------

def test_a_reading_shows_the_chart_the_engine_computed(client):
    body = read(client).text
    assert "庚午" in body      # year pillar
    assert "辛巳" in body      # month pillar
    assert "立夏" in body      # the sectional term
    assert "日主" in body


def test_a_reading_shows_the_disclosure_that_went_out(client):
    assert AI_DISCLOSURE_SHORT in read(client).text


def test_the_rendered_prompt_carries_no_birth_datetime(client):
    """The demo shows the prompt verbatim, so if the birth datetime ever
    creeps back into format_for_prompt this fails too."""
    body = read(client).text
    assert "1990-05-15T" not in body
    assert "07:30:00" not in body


def test_a_three_pillar_chart_is_shown_as_unknown_not_omitted(client):
    body = read(client, birth_time="").text
    assert "不明" in body
    assert "三柱" in body


# --- It cannot bypass anything ---------------------------------------------

def test_crisis_language_is_redirected_and_the_model_is_not_called(store,
                                                                  config):
    class ExplodingModel:
        def generate(self, *args, **kwargs):
            raise AssertionError("the demo reached a model with crisis text")

    client = make_client(store, config, ExplodingModel())
    body = read(client, question="もう死にたいです").text
    assert "0570-064-556" in body
    assert "redirect_crisis" in body or "REDIRECT_CRISIS" in body


def test_the_crisis_reply_shown_carries_no_disclosure(client):
    """Same rule as LINE. The page renders what was cleared to send, so it
    shows the absence rather than papering over it."""
    body = read(client, question="もう死にたいです").text
    assert "0570-064-556" in body
    assert AI_DISCLOSURE_SHORT not in body


def test_a_blocked_reading_never_appears_in_the_reply(store, config):
    client = make_client(store, config, StubModel("必ず良い方向に向かいます"))
    body = read(client).text

    assert "必ず良い方向に向かいます" not in "".join(bubbles(body))
    assert "お出しできる鑑定文をご用意できませんでした" in "".join(bubbles(body))


def test_a_blocked_reading_is_shown_to_the_operator_with_the_reason(store,
                                                                   config):
    """E5: a block is a prompt defect, and it gets fixed in the prompt where
    the practitioner can see it. Hiding the text that tripped the filter
    would make this page useless for the job it exists to do."""
    client = make_client(store, config, StubModel("必ず良い方向に向かいます"))
    body = read(client).text
    assert "必ず良い方向に向かいます" in body     # in the operator pane
    assert "検査前" in body                      # labelled as pre-screening
    assert "景品表示法" in body                  # with the reason


def test_a_boundary_chart_shows_the_hand_off_to_a_human(client):
    body = read(client, birth_date="2020-08-07", birth_time="").text
    assert "担当者が確認" in body
    assert "受付番号" in body


def test_the_demo_spends_against_the_same_budget_guard(store, config):
    client = make_client(store, config, budget=0.0)
    body = read(client).text
    assert "混み合っており" in body       # SERVICE_PAUSED
    assert list(store.iter_llm_usage()) == []


def test_the_demo_consumes_the_same_free_quota(client, store):
    for _ in range(3):
        read(client)
    assert store.free_quota_remaining(DEMO_USER, 3) == 0
    assert "本日分の無料鑑定はここまで" in read(client).text


def test_reset_restores_the_quota(client, store):
    for _ in range(3):
        read(client)
    assert client.get("/reset", follow_redirects=False).status_code == 303
    assert store.free_quota_remaining(DEMO_USER, 3) == 3


# --- Escaping --------------------------------------------------------------

def test_user_input_is_escaped(client):
    """The question is echoed back into the page. It is attacker-controlled
    in exactly the way a real user's message is."""
    body = read(client, question="<script>alert(1)</script>恋愛運").text
    assert "<script>alert(1)</script>" not in body
    assert "&lt;script&gt;" in body


def test_a_malformed_birth_field_does_not_500(client):
    assert read(client, birth_date="not a date").status_code == 200
    assert read(client, birth_date="1990-05-15", birth_time="99:99").status_code == 200


def test_an_empty_form_does_not_500(client):
    assert client.post("/reading", headers=FORM, data={}).status_code == 200


# --- Structural ------------------------------------------------------------

def test_the_demo_builds_a_stub_model_unless_told_otherwise(config, store):
    """No code path here constructs an OpenAI client on its own. `--live` is
    the only way, and it still goes through the budget guard."""
    import inspect

    from bot import demo
    source = inspect.getsource(demo.create_demo_app)
    assert "if live:" in source
    assert source.index("StubModel()") > source.index("if live:")


def test_the_page_says_the_prompts_are_placeholders(client):
    assert "PLACEHOLDER" in client.get("/").text.upper()

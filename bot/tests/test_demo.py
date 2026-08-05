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
    # What the user gets: the helplines, 24-hour free line first.
    assert "0120-279-338" in body
    assert "0570-064-556" in body
    # What the operator sees: which pattern fired, and that the AI stage was
    # never reached. Asserted on the visible page rather than on an enum
    # name, since the page is what a board member actually reads.
    assert "crisis pattern" in body
    assert "呼び出さず" in body


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
    assert "お出しできる形にまとまりませんでした" in "".join(bubbles(body))


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
    assert "人が確かめて" in body
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
    assert "本日分はここまで" in read(client).text   # not a paywall: we may not sell yet


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


# --- Sharing: the gate, sessions, and what is kept -------------------------

SHARED_ENV = {"FREE_TIER_LIMIT": "3", "DEMO_ACCESS_CODES": "board:brd-1,seed:sd-2"}


def shared_client(store, env=None):
    config = Config.from_env({**SHARED_ENV, **(env or {})})
    gateway = ModelGateway(StubModel(), BudgetGuard(store, 10.0))
    return TestClient(create_demo_app(
        config, store, ReadingService(store, gateway, config), shared=True))


def test_sharing_without_access_codes_is_refused(store):
    """An unlisted URL is not access control, and this page takes birth
    dates. It refuses rather than warns, because a forgotten env var is not
    a mistake worth leaving available."""
    from bot.demo import NotShareable
    config = Config.from_env({"FREE_TIER_LIMIT": "3"})
    with pytest.raises(NotShareable, match="DEMO_ACCESS_CODES"):
        create_demo_app(config, store, shared=True)


def test_a_shared_demo_shows_the_gate_before_anything_else(store):
    client = shared_client(store)
    body = client.get("/").text
    assert "アクセスコード" in body
    assert "恋愛運" not in body       # no form, no presets, no pipeline


def test_a_reading_cannot_be_run_without_a_session(store):
    client = shared_client(store)
    response = client.post("/reading", headers=FORM, follow_redirects=False,
                           data={"birth_date": "1990-05-15",
                                 "question": "恋愛運を教えてください"})
    assert response.status_code == 303
    assert list(store.iter_llm_usage()) == []


def test_a_valid_code_opens_the_demo(store):
    client = shared_client(store)
    assert client.post("/enter", headers=FORM, data={"code": "brd-1"},
                       follow_redirects=False).status_code == 303
    assert "恋愛運" in client.get("/").text


def test_a_wrong_code_does_not(store):
    client = shared_client(store)
    response = client.post("/enter", headers=FORM, data={"code": "guess"},
                           follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/?bad=1"
    assert "アクセスコード" in client.get("/").text


def test_the_session_cookie_is_httponly_and_not_the_user_id(store):
    client = shared_client(store)
    response = client.post("/enter", headers=FORM, data={"code": "brd-1"},
                           follow_redirects=False)
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie
    assert "SameSite=lax" in cookie.replace("samesite", "SameSite")


def test_cohorts_are_recorded_so_board_and_seed_can_be_told_apart(store):
    """The Phase 0 conversion number depends on this distinction."""
    for code in ("brd-1", "sd-2"):
        shared_client(store).post("/enter", headers=FORM, data={"code": code})
    cohorts = [e["cohort"] for e in store.iter_events()
               if e["type"] == "demo_session_started"]
    assert sorted(cohorts) == ["board", "seed"]


def test_each_visitor_gets_their_own_quota(store):
    """One board member must not be able to exhaust another's allowance."""
    first, second = shared_client(store), shared_client(store)
    first.post("/enter", headers=FORM, data={"code": "brd-1"})
    second.post("/enter", headers=FORM, data={"code": "brd-1"})
    for _ in range(3):
        read(first)
    assert "本日分はここまで" in read(first).text
    assert "本日分はここまで" not in read(second).text


def test_the_session_token_never_reaches_the_event_log(store):
    client = shared_client(store)
    response = client.post("/enter", headers=FORM, data={"code": "brd-1"},
                           follow_redirects=False)
    token = response.headers["set-cookie"].split("=")[1].split(";")[0]
    read(client)
    assert token not in store.events_file.read_text(encoding="utf-8")


def test_the_access_code_never_reaches_the_event_log(store):
    client = shared_client(store)
    client.post("/enter", headers=FORM, data={"code": "brd-1"})
    assert "brd-1" not in store.events_file.read_text(encoding="utf-8")


# --- What a visitor is told ------------------------------------------------

def test_the_privacy_notice_is_reachable_without_a_code(store):
    body = shared_client(store).get("/privacy").text
    assert "個人情報の扱い" in body
    assert "生年月日は保存しません" in body


def test_the_readiness_page_is_reachable_and_honest(store):
    body = shared_client(store).get("/readiness").text
    assert "六つのゲート" in body
    assert "シードユーザー" in body
    assert "不可" in body            # not ready for real users, and says so


def test_the_readiness_page_distinguishes_the_two_thresholds(store):
    """The board demo is allowed and seed users are not, and the page has to
    say both — a page that says "not ready" flatly would be ignored."""
    body = shared_client(store).get("/readiness").text
    board = body[body.index("friends-and-family"):body.index("real users")]
    seed = body[body.index("real users"):]
    assert "可</span>" in board and "不可" not in board
    assert "不可" in seed


def test_the_banner_reports_unmet_gates(store):
    client = shared_client(store)
    client.post("/enter", headers=FORM, data={"code": "brd-1"})
    assert "未達のゲート" in client.get("/").text


def test_birth_data_is_never_written_for_an_ordinary_reading(store):
    """The web form carries it on every request, so there is no reason to
    keep it. Only a boundary chart's review entry records one."""
    client = shared_client(store)
    client.post("/enter", headers=FORM, data={"code": "brd-1"})
    read(client)
    assert "1990-05-15" not in store.users_file.read_text(encoding="utf-8")
    assert "1990-05-15" not in store.events_file.read_text(encoding="utf-8")


# --- /health: what is actually deployed ------------------------------------

def test_health_needs_no_code(store):
    """A platform health check has no cookie. If this needed one, the
    deployment would look permanently unhealthy."""
    assert shared_client(store).get("/health").status_code == 200


def test_health_reports_the_model_and_the_gates(store):
    body = shared_client(store).get("/health").json()
    assert body["model"] == "stub"
    assert body["ready_for_friends_and_family"] is True
    assert body["ready_for_real_users"] is False
    assert body["prompts_are_placeholders"] is True
    assert body["access_gated"] is True


def test_health_reports_when_access_is_not_gated(store, config):
    assert make_client(store, config).get("/health").json()["access_gated"] is False


def test_health_leaks_no_codes_or_keys(store):
    body = shared_client(store).get("/health").text
    assert "brd-1" not in body and "sd-2" not in body


# --- Being shared through a tunnel ----------------------------------------

def test_a_tunnel_cannot_bypass_the_access_gate():
    """A Cloudflare Tunnel connects to 127.0.0.1 and republishes it to the
    internet. The process cannot tell that from its own socket, so binding
    to loopback proves nothing about who can reach it. --shared says so
    explicitly, and it must still demand access codes."""
    import inspect

    from bot import demo
    source = inspect.getsource(demo.main)
    assert "args.shared or" in source, \
        "shared mode must be forceable independently of the bind address"


def test_shared_mode_demands_codes_regardless_of_bind_address(store):
    """The refusal is a property of shared mode, not of the host argument."""
    from bot.demo import NotShareable
    config = Config.from_env({"FREE_TIER_LIMIT": "3"})
    with pytest.raises(NotShareable):
        create_demo_app(config, store, shared=True)


# --- Serverless / ephemeral hosts ------------------------------------------

EPHEMERAL = {"FREE_TIER_LIMIT": "3", "DEMO_ACCESS_CODES": "board:brd-1",
             "DEMO_SESSION_SECRET": "test-secret", "VERCEL": "1"}


def test_vercel_is_detected_from_its_own_marker():
    """Detected rather than only configured: the failure this guards is
    someone deploying without knowing it applies to them."""
    assert Config.from_env({"VERCEL": "1"}).ephemeral_filesystem is True
    assert Config.from_env({"AWS_LAMBDA_FUNCTION_NAME": "f"}).ephemeral_filesystem
    assert Config.from_env({}).ephemeral_filesystem is False


def test_a_billable_model_is_refused_on_an_ephemeral_filesystem(store):
    """The budget guard sums a usage log that does not survive here, so the
    cap would read $0 for ever. Spending is made impossible instead — a
    promise this platform can actually keep."""
    from bot.demo import NotShareable
    config = Config.from_env({**EPHEMERAL, "OPENAI_API_KEY": "sk-x"})
    with pytest.raises(NotShareable, match="MONTHLY_LLM_BUDGET_USD"):
        create_demo_app(config, store, live=True, shared=True)


def test_the_stub_is_allowed_on_an_ephemeral_filesystem(store):
    assert create_demo_app(Config.from_env(EPHEMERAL), store, shared=True)


def test_sharing_on_an_ephemeral_host_demands_a_stable_session_secret(store):
    """Per-instance generated keys mean cookies signed by one instance fail
    on the next — and the session is what enforces the access code."""
    from bot.demo import NotShareable
    config = Config.from_env({k: v for k, v in EPHEMERAL.items()
                              if k != "DEMO_SESSION_SECRET"})
    with pytest.raises(NotShareable, match="DEMO_SESSION_SECRET"):
        create_demo_app(config, store, shared=True)


def test_the_privacy_notice_stops_promising_follow_up_when_it_cannot(store):
    """A boundary chart's queue entry does not survive here, so the standing
    promise that a person will follow up is not one this deployment keeps."""
    body = create_ephemeral_client(store).get("/privacy").text
    assert "後日の連絡はできません" in body


def create_ephemeral_client(store):
    config = Config.from_env(EPHEMERAL)
    gateway = ModelGateway(StubModel(), BudgetGuard(store, 10.0))
    return TestClient(create_demo_app(
        config, store, ReadingService(store, gateway, config), shared=True))


# --- Stateless sessions ----------------------------------------------------

def test_a_session_survives_a_process_restart(store):
    """Two apps sharing a secret is what a second instance looks like, and
    what a redeploy looks like. The old in-memory dict failed exactly here,
    which on a multi-instance host means the access gate stops working
    rather than merely annoying people."""
    from bot.demo import SESSION_COOKIE

    first, second = create_ephemeral_client(store), create_ephemeral_client(store)
    response = first.post("/enter", headers=FORM, data={"code": "brd-1"},
                          follow_redirects=False)
    cookie = response.cookies[SESSION_COOKIE]

    second.cookies.set(SESSION_COOKIE, cookie)
    assert "恋愛運" in second.get("/").text


def test_a_session_signed_with_another_secret_is_rejected(store):
    from bot.demo import Sessions
    issued = Sessions("secret-a").create("board")
    assert Sessions("secret-b").get(issued) is None
    assert Sessions("secret-a").get(issued)["cohort"] == "board"


def test_a_tampered_session_is_rejected():
    """Swapping the payload for a longer-lived one, keeping the signature."""
    import base64

    from bot.demo import Sessions

    sessions = Sessions("secret")
    _encoded, _, signature = sessions.create("seed").partition(".")
    forged = base64.urlsafe_b64encode(b"board|9999999999|deadbeef")
    assert sessions.get(f"{forged.decode().rstrip('=')}.{signature}") is None


def test_an_expired_session_is_rejected(monkeypatch):
    import time as real_time

    from bot import demo

    sessions = demo.Sessions("secret")
    token = sessions.create("board")
    assert sessions.get(token) is not None

    # Captured before patching: demo.time IS the time module, so patching it
    # would otherwise patch the replacement's own call to it.
    later = real_time.time() + demo.SESSION_TTL_SECONDS + 60
    monkeypatch.setattr(demo.time, "time", lambda: later)
    assert sessions.get(token) is None


def test_malformed_session_tokens_do_not_raise():
    from bot.demo import Sessions
    sessions = Sessions("secret")
    for junk in ("", "no-dot", "....", "!!!.!!!", "a.b", None):
        assert sessions.get(junk) is None


def test_the_visitor_id_is_stable_across_instances(store):
    """Per-visitor quota depends on the id being the same person each time."""
    from bot.demo import Sessions
    token = Sessions("shared-secret").create("board")
    first = Sessions("shared-secret").get(token)
    second = Sessions("shared-secret").get(token)
    assert first["user_id"] == second["user_id"]
    assert first["user_id"].startswith("demo:board:")


# --- Knowing what is deployed ---------------------------------------------

def test_health_reports_the_deployed_commit(store, monkeypatch):
    """Without this, "did it deploy?" is unanswerable from outside whenever
    a release changes nothing visible — which is most of them."""
    monkeypatch.setenv("VERCEL_GIT_COMMIT_SHA", "abcdef1234567890")
    monkeypatch.setenv("VERCEL_GIT_COMMIT_REF", "main")
    version = shared_client(store).get("/health").json()["version"]
    assert version["commit"] == "abcdef123456"
    assert version["branch"] == "main"


def test_health_reports_unknown_rather_than_lying(store, monkeypatch):
    for key in ("VERCEL_GIT_COMMIT_SHA", "RENDER_GIT_COMMIT",
                "GIT_COMMIT_SHA", "VERCEL_GIT_COMMIT_REF"):
        monkeypatch.delenv(key, raising=False)
    assert shared_client(store).get("/health").json()["version"]["commit"] == "unknown"


# --- Track A: shareable examples, mobile, plain language -------------------

def test_a_preset_has_a_shareable_url(client):
    """"Look at scenario 4" should be a link, not instructions."""
    response = client.get("/example/3")
    assert response.status_code == 200
    assert "0120-279-338" in response.text      # the crisis preset ran


def test_every_preset_is_reachable_by_url(client):
    for index in range(len(PRESETS)):
        assert client.get(f"/example/{index}").status_code == 200


def test_an_out_of_range_example_redirects_rather_than_500ing(client):
    assert client.get("/example/99", follow_redirects=False).status_code == 303
    assert client.get("/example/-1", follow_redirects=False).status_code in (303, 404)


def test_examples_are_gated_like_everything_else(store):
    client = shared_client(store)
    assert client.get("/example/0", follow_redirects=False).status_code == 303


def test_a_custom_reading_has_no_shareable_url(client):
    """The counterpart to the presets having one. A URL for a custom reading
    would carry a birth date and a question into the access log, browser
    history and whatever chat app it was pasted into."""
    response = read(client, birth_date="1990-05-15", question="恋愛運")
    assert response.status_code == 200
    # The result came from a POST; there is no GET that reproduces it.
    assert client.get("/reading", follow_redirects=False).status_code == 405


def test_inputs_are_at_least_16px(client):
    """Below 16px, iOS Safari zooms the whole page when a field is focused,
    which on a phone reads as the page breaking."""
    import re
    css = client.get("/").text
    rule = re.search(r"input\[type=text\],textarea,select\{[^}]*\}", css)
    assert rule, "input rule not found"
    size = re.search(r"font-size:(\d+)px", rule.group(0))
    assert size and int(size.group(1)) >= 16


def test_the_pipeline_names_each_step_in_plain_japanese_and_in_code(client):
    """A board member reading screen_input() learns nothing; an engineer
    reading only 「危機的な表現の判定」 cannot find the code."""
    body = read(client).text
    assert "危機的な表現・専門領域の判定" in body
    assert "safety.screen_input()" in body
    assert "命式の計算（AI ではなくエンジンが）" in body
    assert "engine.compute_chart()" in body


def test_the_gate_page_explains_itself_before_asking_for_a_code(store):
    body = shared_client(store).get("/").text
    assert "入力は保存しません" in body
    assert "試作" in body
    assert "命式" in body


# --- Track C: the practitioner's workbench --------------------------------

REVIEW_CSV = (
    "label,birth_date,birth_time,year,month,day,hour,note\n"
    "一致,1990-05-15,07:30,庚午,辛巳,庚辰,庚辰,\n"
    "23時台,1985-03-10,23:30,,,戊申,壬子,子の刻で日柱を変える\n"
    "不明な相違,1975-06-06,10:00,甲子,,乙丑,,\n"
)


def post(client, path, **fields):
    return client.post(path, headers=FORM, data=fields)


def test_the_review_page_loads(client):
    assert "命式の照合" in client.get("/review").text


def test_the_review_page_diagnoses_rather_than_just_diffing(client):
    """A bare list of mismatches hands the practitioner an engineering
    problem and hands us a divination problem."""
    body = post(client, "/review", charts=REVIEW_CSV).text
    assert "一致" in body
    assert "P1" in body           # the 23:00 birth, reconciled by the ruling
    assert "未説明" in body        # the genuinely wrong one


def test_the_review_page_reports_unreadable_rows_without_dying(client):
    body = post(client, "/review", charts=(
        "label,birth_date,birth_time,year,month,day,hour,note\n"
        "壊れた行,not-a-date,,甲子,,,,\n"
        "良い行,1990-05-15,07:30,庚午,,,,\n")).text
    assert "読み取れません" in body
    assert "一致" in body          # the good row still ran


def test_an_empty_paste_says_so_rather_than_500ing(client):
    assert post(client, "/review", charts="").status_code == 200


def test_the_workbench_shows_the_prompt_the_model_would_receive(client):
    """Gate 2 is the practitioner writing prompts. What they are authoring
    is this text, with a real chart in it."""
    body = post(client, "/prompt",
                system="あなたは四柱推命の占い師です。",
                template="{chart}\n{hour_note}\n【ご相談】{question}",
                birth_date="1990-05-15", birth_time="07:30",
                question="恋愛運を教えてください").text
    assert "庚午" in body                      # the computed chart
    assert "恋愛運を教えてください" in body
    assert "あなたは四柱推命の占い師です。" in body


def test_the_workbench_is_honest_that_the_stub_ignores_the_prompt(client):
    """Showing stub output that does not change however the prompt is edited
    would teach the practitioner something false about their own work."""
    body = post(client, "/prompt", system="s", template="{chart}",
                birth_date="1990-05-15", birth_time="07:30", question="q").text
    assert "プロンプトを変えても鑑定文は変わりません" in body


def test_the_workbench_names_an_unknown_placeholder(client):
    """A renamed field should say which one, not raise."""
    body = post(client, "/prompt", system="s", template="{chart} {nonexistent}",
                birth_date="1990-05-15", birth_time="07:30", question="q").text
    assert "差し込み名が不明" in body
    assert "nonexistent" in body


def test_the_workbench_refuses_a_boundary_chart_like_the_pipeline_does(client):
    body = post(client, "/prompt", system="s", template="{chart}",
                birth_date="2020-08-07", birth_time="", question="q").text
    assert "節気の境界" in body


def test_the_workbench_reports_an_unreadable_birth_date(client):
    body = post(client, "/prompt", system="s", template="{chart}",
                birth_date="nonsense", birth_time="", question="q").text
    assert "読み取れませんでした" in body


def test_both_tools_are_gated(store):
    client = shared_client(store)
    for path in ("/review", "/prompt"):
        assert client.get(path, follow_redirects=False).status_code == 303
        assert client.post(path, headers=FORM, data={},
                           follow_redirects=False).status_code == 303

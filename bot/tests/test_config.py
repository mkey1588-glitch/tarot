"""Tests for configuration loading.

The property worth protecting here is that the credential-free paths stay
credential-free. Sprint 01 requires `pytest` and `python -m bot.test_local`
to run with no LINE account, no API key and no network, and the easiest way
to lose that is a module that validates everything the moment it is imported.
"""

import pytest

from bot.config import (
    Config, MissingConfig, UnpricedModel, load_env, price_for,
    MODEL_PRICES_USD_PER_MTOK,
)


# --- Reading the environment ----------------------------------------------

def test_defaults_need_no_environment_at_all():
    cfg = Config.from_env({})
    assert cfg.line_channel_secret is None
    assert cfg.openai_api_key is None
    assert cfg.free_tier_limit == 3
    assert cfg.monthly_llm_budget_usd == 50.0


def test_values_are_read_and_typed():
    cfg = Config.from_env({
        "LINE_CHANNEL_SECRET": "secret",
        "OPENAI_API_KEY": "sk-test",
        "FREE_TIER_LIMIT": "5",
        "MONTHLY_LLM_BUDGET_USD": "12.5",
        "DEEP_READING_PRICE_JPY": "500",
    })
    assert cfg.line_channel_secret == "secret"
    assert cfg.free_tier_limit == 5
    assert cfg.monthly_llm_budget_usd == 12.5
    assert cfg.deep_reading_price_jpy == 500


def test_blank_values_fall_back_to_defaults():
    """An unfilled .env line is `KEY=`, which is not a configured value."""
    cfg = Config.from_env({"OPENAI_API_KEY": "  ", "FREE_TIER_LIMIT": ""})
    assert cfg.openai_api_key is None
    assert cfg.free_tier_limit == 3


def test_a_non_numeric_number_fails_loudly():
    with pytest.raises(MissingConfig, match="FREE_TIER_LIMIT"):
        Config.from_env({"FREE_TIER_LIMIT": "three"})


# --- Validation per capability --------------------------------------------

def test_missing_line_credentials_name_every_missing_key():
    """One key at a time turns deployment into a guessing game."""
    with pytest.raises(MissingConfig) as exc:
        Config.from_env({}).require_line()
    assert "LINE_CHANNEL_ACCESS_TOKEN" in str(exc.value)
    assert "LINE_CHANNEL_SECRET" in str(exc.value)


def test_missing_api_key_fails_llm_validation():
    with pytest.raises(MissingConfig, match="OPENAI_API_KEY"):
        Config.from_env({}).require_llm()


def test_validation_is_not_run_on_construction():
    """The whole point: an incomplete config is constructible, so the
    credential-free paths can use the parts of it that need no credentials."""
    cfg = Config.from_env({})
    assert cfg.free_tier_limit == 3
    assert cfg.data_dir.name == "data"


def test_require_all_reports_line_and_llm_problems_together():
    with pytest.raises(MissingConfig) as exc:
        Config.from_env({}).require_all()
    assert "LINE_CHANNEL_SECRET" in str(exc.value)
    assert "OPENAI_API_KEY" in str(exc.value)


# --- Pricing ---------------------------------------------------------------

def test_configured_models_are_priced():
    """A model the guard cannot price is a model whose spend is invisible."""
    cfg = Config.from_env({})
    assert price_for(cfg.model_free)
    assert price_for(cfg.model_paid)


def test_an_unpriced_model_is_fatal_rather_than_free():
    with pytest.raises(UnpricedModel):
        price_for("gpt-9-imaginary")


def test_an_unpriced_model_fails_llm_validation():
    """Caught at startup, not on the first billed call."""
    cfg = Config.from_env({"OPENAI_API_KEY": "sk-test",
                           "LLM_MODEL_PAID": "gpt-9-imaginary"})
    with pytest.raises(UnpricedModel):
        cfg.require_llm()


def test_output_tokens_are_priced_above_input_tokens():
    """A transposed price pair would under-report spend on every call, which
    is the direction that empties a budget quietly."""
    for model, (inp, out) in MODEL_PRICES_USD_PER_MTOK.items():
        assert out > inp > 0, model


# --- Environment loading ---------------------------------------------------

def test_load_env_is_never_called_on_import():
    """Guards the property directly: importing bot.config must not read the
    developer's .env, or these tests stop meaning the same thing in CI."""
    import bot.config
    source = open(bot.config.__file__, encoding="utf-8").read()
    body = source.split('def load_env')[0]
    assert "load_dotenv(" not in body


def test_load_env_survives_a_missing_file():
    load_env(Config.from_env({}).data_dir / "definitely-not-here.env")

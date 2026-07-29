"""
Configuration.

Two rules shape this module.

First, **nothing here loads `.env` on import.** `pytest` and
`python -m bot.test_local` must run identically on a laptop holding real
credentials and in CI holding none, and a module that reads the developer's
`.env` the moment it is imported cannot promise that. `load_env()` is
called explicitly by the processes that want it — which is `app.py`, and
nothing else.

Second, **a missing key fails loudly at the point of use, not at import.**
The bot needs LINE credentials; the local runner needs none; the cost guard
needs neither. Validating everything at import would make the credential-free
paths that Sprint 01 requires impossible to run.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent


class MissingConfig(RuntimeError):
    """Raised when a required key is absent. Lists every missing key at once.

    One key at a time turns configuring a deployment into a guessing game.
    """


# --- Model pricing ---------------------------------------------------------

# USD per million tokens, (input, output). The cost guard is only as honest
# as this table, so it is checked in rather than inferred.
#
# TODO(cost): re-confirm against current API pricing before Phase 1. These
# are the figures the Phase 0 budget was sized on.
MODEL_PRICES_USD_PER_MTOK: Dict[str, Tuple[float, float]] = {
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.00),
}


class UnpricedModel(RuntimeError):
    """Raised when a model has no entry in MODEL_PRICES_USD_PER_MTOK.

    Deliberately fatal rather than defaulting to zero. A model the guard
    cannot price is a model whose spend is invisible to it, and an invisible
    runaway is exactly what MONTHLY_LLM_BUDGET_USD exists to stop.
    """


def _access_codes(raw: Optional[str]) -> Dict[str, str]:
    """Parse "board:abc,seed:def" into {code: cohort}.

    Keyed by code so a lookup is O(1) and so two cohorts cannot collide on
    the same code silently. A bare "abc" is treated as cohort "guest".

    Cohorts exist so the event log can tell a board member's session from a
    seed user's, which is the distinction the whole Phase 0 number rests on.
    """
    codes: Dict[str, str] = {}
    for chunk in (raw or "").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        cohort, _, code = chunk.rpartition(":")
        code = code.strip()
        if code:
            codes[code] = (cohort.strip() or "guest")
    return codes


def price_for(model: str) -> Tuple[float, float]:
    if model not in MODEL_PRICES_USD_PER_MTOK:
        raise UnpricedModel(
            f"no price for model {model!r}; add it to "
            "MODEL_PRICES_USD_PER_MTOK. Refusing to bill against an unknown "
            "rate."
        )
    return MODEL_PRICES_USD_PER_MTOK[model]


# --- Configuration ---------------------------------------------------------

@dataclass(frozen=True)
class Config:
    # LINE. Absent in every credential-free path.
    line_channel_access_token: Optional[str] = None
    line_channel_secret: Optional[str] = None

    # LLM. Cheap model for free content, stronger one only for paid readings.
    openai_api_key: Optional[str] = None
    model_free: str = "gpt-4o-mini"
    model_paid: str = "gpt-4o"

    # Phase 0 business parameters.
    free_tier_limit: int = 3
    deep_reading_price_jpy: int = 300

    # Ops.
    data_dir: Path = field(default=REPO_ROOT / "data")
    monthly_llm_budget_usd: float = 50.0
    log_level: str = "INFO"
    port: int = 8000

    # Gates /admin/*. The review queue holds birth data, which is personal
    # information under 個人情報保護法, so those endpoints are disabled
    # rather than public when this is unset.
    admin_token: Optional[str] = None

    # Payments. Present only to answer "is the 特商法 notice required yet".
    stripe_secret_key: Optional[str] = None

    # Set to the date counsel signed off the user-facing copy. Read by
    # bot/readiness.py; it is not something an engineer should be setting.
    legal_review_completed_on: Optional[str] = None

    # --- Shared demo (bot/demo.py) ---------------------------------------
    # "label:code,label:code". No codes means the demo refuses to bind to
    # anything but loopback — an unlisted URL is not access control.
    demo_access_codes: Dict[str, str] = field(default_factory=dict)
    # Whether the shared demo keeps anything on disk between restarts.
    demo_persist: bool = False

    @classmethod
    def from_env(cls, env: Optional[Dict[str, str]] = None) -> "Config":
        """Build from a mapping, defaulting to os.environ.

        Takes `env` explicitly so tests do not have to mutate the process
        environment to exercise configuration.
        """
        e = os.environ if env is None else env

        def _int(key: str, default: int) -> int:
            raw = e.get(key, "").strip()
            if not raw:
                return default
            try:
                return int(raw)
            except ValueError:
                raise MissingConfig(f"{key} must be an integer, got {raw!r}")

        def _float(key: str, default: float) -> float:
            raw = e.get(key, "").strip()
            if not raw:
                return default
            try:
                return float(raw)
            except ValueError:
                raise MissingConfig(f"{key} must be a number, got {raw!r}")

        def _str(key: str) -> Optional[str]:
            return (e.get(key) or "").strip() or None

        data_dir = _str("DATA_DIR")
        return cls(
            line_channel_access_token=_str("LINE_CHANNEL_ACCESS_TOKEN"),
            line_channel_secret=_str("LINE_CHANNEL_SECRET"),
            openai_api_key=_str("OPENAI_API_KEY"),
            model_free=_str("LLM_MODEL_FREE") or "gpt-4o-mini",
            model_paid=_str("LLM_MODEL_PAID") or "gpt-4o",
            free_tier_limit=_int("FREE_TIER_LIMIT", 3),
            deep_reading_price_jpy=_int("DEEP_READING_PRICE_JPY", 300),
            data_dir=Path(data_dir) if data_dir else REPO_ROOT / "data",
            monthly_llm_budget_usd=_float("MONTHLY_LLM_BUDGET_USD", 50.0),
            log_level=_str("LOG_LEVEL") or "INFO",
            port=_int("PORT", 8000),
            admin_token=_str("ADMIN_TOKEN"),
            stripe_secret_key=_str("STRIPE_SECRET_KEY"),
            legal_review_completed_on=_str("LEGAL_REVIEW_COMPLETED_ON"),
            demo_access_codes=_access_codes(_str("DEMO_ACCESS_CODES")),
            demo_persist=(_str("DEMO_PERSIST") or "").lower()
                         in {"1", "true", "yes"},
        )

    # --- Validation, per capability rather than all at once ---------------

    def require_line(self) -> None:
        """Fail unless we can verify a webhook signature and send a reply."""
        missing = [
            name for name, value in (
                ("LINE_CHANNEL_ACCESS_TOKEN", self.line_channel_access_token),
                ("LINE_CHANNEL_SECRET", self.line_channel_secret),
            ) if not value
        ]
        if missing:
            raise MissingConfig(
                "LINE credentials missing: " + ", ".join(missing) +
                ". Copy .env.example to .env and fill them in."
            )

    def require_llm(self) -> None:
        """Fail unless we can call a model, and unless we can price it."""
        if not self.openai_api_key:
            raise MissingConfig(
                "OPENAI_API_KEY missing. Copy .env.example to .env and fill "
                "it in, or use the stub model in bot/test_local.py."
            )
        for model in (self.model_free, self.model_paid):
            price_for(model)  # raises UnpricedModel

    def require_all(self) -> None:
        """Everything a live deployment needs. Called at app startup."""
        errors = []
        for check in (self.require_line, self.require_llm):
            try:
                check()
            except (MissingConfig, UnpricedModel) as exc:
                errors.append(str(exc))
        if errors:
            raise MissingConfig("\n".join(errors))


def load_env(path: Optional[Path] = None) -> None:
    """Load `.env` into the process environment. Call explicitly, never on import.

    A no-op if python-dotenv is absent, so the credential-free paths do not
    acquire a dependency they have no use for.
    """
    try:
        from dotenv import load_dotenv
    except ImportError:  # pragma: no cover - dotenv is in requirements.txt
        return
    load_dotenv(path or REPO_ROOT / ".env")

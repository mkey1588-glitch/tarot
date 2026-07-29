"""
End-to-end runner for the reading pipeline. No credentials, no network, no spend.

    python -m bot.test_local

It prints, for each scenario, what the user said, what the input screen
decided, the chart the engine computed, the exact prompt the model was
handed, what came back, what the output screen decided, and the final reply.

Deliberately does NOT call `load_env()`. There is nothing here that could
use a credential — the model is a stub and the transport is a recorder —
and not loading the file is the difference between believing that and
knowing it.

Writes to a temporary directory, so running this never touches `data/`.

This is not a pytest file despite the name, which the sprint fixes. It is
excluded from collection by `testpaths` in pytest.ini; the pytest suite for
this pipeline is in bot/tests/.
"""

from __future__ import annotations

import logging
import shutil
import tempfile
from datetime import date, datetime, time, timedelta, timezone
from typing import Optional

from bot.config import Config
from bot.cost import BudgetGuard
from bot.llm import ModelGateway, ScreenedPrompt, StubModel
from bot.outbound import NullTransport
from bot.prompts_ja import PROMPTS_ARE_PLACEHOLDERS, startup_warning
from bot.reading import BirthData, ReadingService
from bot.safety import screen_input, screen_output
from bot.storage import JST, Storage
from engine.solar import solar_term_instant

RULE = "=" * 72
THIN = "-" * 72


class RecordingStub(StubModel):
    """A stub that remembers the prompt it was given and what it returned,
    so the runner shows what the model actually saw and said rather than
    what we assume it did."""

    def __init__(self, reply: Optional[str] = None):
        super().__init__(reply)
        self.last_prompt: Optional[ScreenedPrompt] = None
        self.last_text: Optional[str] = None

    def generate(self, prompt, **kwargs):
        self.last_prompt = prompt
        completion = super().generate(prompt, **kwargs)
        self.last_text = completion.text
        return completion


def _boundary_birth_date() -> date:
    """A birth date on which a sectional term falls. With no reported time
    the month pillar is ambiguous across the whole day, so the chart must go
    to a human rather than be guessed at."""
    instant = solar_term_instant(135.0, datetime(2020, 8, 7, tzinfo=timezone.utc))
    return instant.astimezone(JST).date()


def run_scenario(label: str, service: ReadingService, model: RecordingStub,
                 transport: NullTransport, *, user_id: str, text: str,
                 birth: Optional[BirthData], tier: str = "free",
                 kind: str = "question") -> None:
    print(f"\n{RULE}\n{label}\n{RULE}")

    print(f"\n[USER SAID]\n  {text}")
    if birth is None:
        print("\n[BIRTH DATA]\n  none on file")
    else:
        print(f"\n[BIRTH DATA]\n  {birth.summary()}"
              f"   (hour_known={birth.hour_known})")

    # Shown for the operator's benefit. The pipeline runs its own screen —
    # this call cannot substitute for it and is not what gates the model.
    inbound = screen_input(text)
    print(f"\n[screen_input]  {inbound.verdict.value}")
    if inbound.reason:
        print(f"  reason: {inbound.reason}")

    model.last_prompt = None
    model.last_text = None
    outcome = service.generate(user_id, text, birth=birth, tier=tier, kind=kind)

    if model.last_prompt is None:
        print("\n[MODEL]  not called")
    else:
        print(f"\n[PROMPT — system]\n{THIN}")
        print(model.last_prompt.system.rstrip())
        print(f"{THIN}\n[PROMPT — user]\n{THIN}")
        print(model.last_prompt.user.rstrip())
        print(THIN)

        print(f"\n[MODEL RETURNED]\n{THIN}")
        print((model.last_text or "").rstrip())
        print(THIN)

        verdict = screen_output(model.last_text or "")
        print(f"\n[screen_output]  {verdict.verdict.value}")
        if verdict.reason:
            print(f"  reason: {verdict.reason}")

    print(f"\n[OUTCOME]  {outcome.outcome}"
          f"   cost=${outcome.cost_usd:.6f}"
          + (f"   review_id={outcome.review_id}" if outcome.review_id else ""))

    print(f"\n[REPLY SENT]  (kind={outcome.message.kind})\n{THIN}")
    print(outcome.message.text)
    print(THIN)

    transport.reply("local-reply-token", outcome.message)


def main() -> None:
    logging.basicConfig(level=logging.WARNING,
                        format="  ! %(levelname)s %(name)s: %(message)s")

    print(RULE)
    print("AI Uranai — local pipeline run")
    print("stub model · no network · no credentials · no spend")
    print(RULE)
    warning = startup_warning()
    if warning:
        print(f"\n  !! {warning}\n")

    data_dir = tempfile.mkdtemp(prefix="uranai-local-")
    try:
        config = Config.from_env({"FREE_TIER_LIMIT": "3",
                                  "MONTHLY_LLM_BUDGET_USD": "10"})
        storage = Storage(data_dir)
        transport = NullTransport()
        model = RecordingStub()
        service = ReadingService(
            storage, ModelGateway(model, BudgetGuard(storage, 10.0)), config)

        with_time = BirthData(date(1990, 5, 15), time(7, 30))
        without_time = BirthData(date(1990, 5, 15))

        run_scenario("1. A reading, birth time known — four pillars",
                     service, model, transport,
                     user_id="U-happy", text="恋愛運を教えてください",
                     birth=with_time)

        run_scenario("2. A reading, birth time unknown — three pillars (P6)",
                     service, model, transport,
                     user_id="U-notime", text="恋愛運を教えてください",
                     birth=without_time)

        run_scenario("3. Crisis language — never reaches the model",
                     service, model, transport,
                     user_id="U-crisis", text="もう死にたいです",
                     birth=with_time)

        run_scenario("4. A medical question — referred, not read",
                     service, model, transport,
                     user_id="U-medical", text="癌は治りますか",
                     birth=with_time)

        run_scenario("5. A boundary chart — routed to a human",
                     service, model, transport,
                     user_id="U-boundary", text="恋愛運を教えてください",
                     birth=BirthData(_boundary_birth_date()))

        run_scenario("6. No birth data on file",
                     service, model, transport,
                     user_id="U-new", text="恋愛運を教えてください", birth=None)

        # 7. The model returns something the outbound screen must block.
        blocking_model = RecordingStub("必ず良い方向に向かいます。絶対に大丈夫です。")
        blocking_service = ReadingService(
            storage, ModelGateway(blocking_model, BudgetGuard(storage, 10.0)),
            config)
        run_scenario("7. A generated reading that trips 景品表示法 — withheld",
                     blocking_service, blocking_model, transport,
                     user_id="U-blocked", text="恋愛運を教えてください",
                     birth=with_time)

        # 8. Free tier exhausted.
        for _ in range(3):
            service.generate("U-quota", "恋愛運を教えて", birth=with_time)
        run_scenario("8. Free tier exhausted",
                     service, model, transport,
                     user_id="U-quota", text="恋愛運を教えてください",
                     birth=with_time)

        # 9. Budget exhausted.
        broke_service = ReadingService(
            storage, ModelGateway(model, BudgetGuard(storage, 0.0)), config)
        run_scenario("9. Monthly budget exhausted — refused before the call",
                     broke_service, model, transport,
                     user_id="U-broke", text="恋愛運を教えてください",
                     birth=with_time)

        # --- Summary ------------------------------------------------------
        guard = BudgetGuard(storage, config.monthly_llm_budget_usd)
        stats = storage.get_stats()

        print(f"\n{RULE}\nSUMMARY\n{RULE}")
        print(f"  replies sent            {len(transport.sent)}")
        print(f"  charts computed         {stats['charts_computed']}")
        print(f"  without a birth time    {stats['charts_without_birth_time']}"
              f"  (rate {stats['missing_birth_time_rate']})")
        print(f"  crisis redirects        {stats['crisis_redirects']}")
        print(f"  open manual reviews     {stats['open_manual_reviews']}")
        print(f"  LLM spend this month    ${guard.month_to_date_usd():.6f}"
              f"  of ${config.monthly_llm_budget_usd:.2f}")

        crisis_events = [e for e in storage.iter_events()
                         if e["type"] == "crisis_redirect"]
        print(f"\n  crisis events recorded  {len(crisis_events)}")
        for event in crisis_events:
            print(f"    {event}   <- pattern and timestamp only, by design")

        print(f"\n  every reply an Outbound  "
              f"{all(hasattr(e['message'], 'kind') for e in transport.sent)}")
        print(f"  prompts are placeholders {PROMPTS_ARE_PLACEHOLDERS}")
        print(f"\n  temporary data dir      {data_dir} (removed)")
        print(RULE)
    finally:
        shutil.rmtree(data_dir, ignore_errors=True)


if __name__ == "__main__":
    main()

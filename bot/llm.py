"""
The model choke point. Every call to a language model in this codebase goes
through `ModelGateway.complete`, and there is no second way.

WHY THIS IS A MODULE AND NOT A FUNCTION CALL AT EACH SITE
---------------------------------------------------------
Two rules have to hold on every path, forever, including paths written
months from now by someone who has not read CLAUDE.md:

  * nothing reaches a model without `screen_input` having allowed it
  * nothing is billed without the monthly budget guard having approved it

Both are enforced here rather than at call sites, because a rule enforced
at a call site is one the next call site forgets. `complete` will not accept
a plain string: it requires a `ScreenedPrompt`, which requires a
`ScreeningToken`, which only `safety.screen_input` can mint. The budget
check happens inside the same method, before the transport is touched.

`bot/tests/test_no_bypass.py` asserts that this is the only module that
imports an LLM SDK.

THE MODEL DOES NOT CALCULATE
---------------------------
Nothing here computes a pillar, an element or a date. The chart arrives
already computed from `engine/` via `chart_service.build_payload`, and the
prompt hands it over as finished text. See CLAUDE.md.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from bot.cost import BudgetGuard, estimate_prompt_tokens
from bot.safety import ScreeningToken, UnscreenedInput

logger = logging.getLogger("uranai.llm")

# A hard ceiling on any single call. The budget guard prices the worst case
# against this, so it is also what makes "worst case" a finite number.
MAX_OUTPUT_TOKENS = 1000


@dataclass(frozen=True)
class ScreenedPrompt:
    """A prompt built from a message that `screen_input` allowed.

    The token is not decoration. It is the reason `complete` can promise
    that screening happened rather than assume it.
    """

    system: str
    user: str
    token: ScreeningToken

    def __post_init__(self) -> None:
        if not isinstance(self.token, ScreeningToken):
            raise UnscreenedInput(
                "ScreenedPrompt requires the ScreeningToken returned by "
                "safety.screen_input(). Screen the message first."
            )


@dataclass(frozen=True)
class Completion:
    text: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float = 0.0


class ModelUnavailable(RuntimeError):
    """The transport failed. Distinct from a refusal by the budget guard."""


# --- Transports ------------------------------------------------------------

class StubModel:
    """A model that costs nothing and touches no network.

    Used by `python -m bot.test_local` and by the tests. It exists so the
    whole pipeline — screening, chart, prompt, screening, disclosure — can
    be exercised end to end with no credentials and no spend, which is a
    Sprint 01 requirement and not a testing convenience.

    Its output is deliberately bland and hedged. It is not a sample of what
    the product sounds like; that is the practitioner's to write.
    """

    def __init__(self, reply: Optional[str] = None):
        self._reply = reply

    def generate(self, prompt: ScreenedPrompt, *, model: str,
                 max_output_tokens: int, temperature: float) -> Completion:
        text = self._reply if self._reply is not None else _STUB_READING
        return Completion(
            text=text,
            model=model,
            prompt_tokens=estimate_prompt_tokens(prompt.system + prompt.user),
            completion_tokens=estimate_prompt_tokens(text),
        )


# PLACEHOLDER — not product copy. A fixed, hedged, deliberately dull reply
# so the pipeline has something to screen. The practitioner writes the real
# voice; nothing here should be mistaken for it.
_STUB_READING = (
    "【スタブ応答】\n"
    "これはテスト用の固定文です。実際の鑑定文ではありません。\n"
    "命式の傾向としては、周囲との関わりのなかで力を発揮しやすい配置と"
    "言われています。\n"
    "迷いを感じるときは、少し時間をおいてから決めるとよいかもしれません。"
)


class OpenAIModel:
    """The real transport. The SDK is imported lazily so that importing this
    module, and therefore running the tests, needs neither the package nor a
    key."""

    def __init__(self, api_key: str):
        self._api_key = api_key
        self._client = None

    def _ensure_client(self):
        if self._client is None:
            try:
                import openai
            except ImportError as exc:  # pragma: no cover
                raise ModelUnavailable(
                    "the openai package is not installed") from exc
            self._client = openai.OpenAI(api_key=self._api_key)
        return self._client

    def generate(self, prompt: ScreenedPrompt, *, model: str,
                 max_output_tokens: int, temperature: float) -> Completion:
        client = self._ensure_client()
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": prompt.system},
                    {"role": "user", "content": prompt.user},
                ],
                max_tokens=max_output_tokens,
                temperature=temperature,
            )
        except Exception as exc:
            # Never let a provider error carry a key or a prompt into a log.
            raise ModelUnavailable(f"{type(exc).__name__}") from exc

        return Completion(
            text=(response.choices[0].message.content or "").strip(),
            model=model,
            prompt_tokens=response.usage.prompt_tokens,
            completion_tokens=response.usage.completion_tokens,
        )


# --- The choke point -------------------------------------------------------

class ModelGateway:
    """The only way to call a model.

    Ordering inside `complete` is load-bearing:

      1. reject anything without a screening token
      2. ask the budget guard, which may refuse
      3. only then touch the transport
      4. record what it cost

    Step 2 before step 3 is what makes the cap a cap.
    """

    def __init__(self, client, guard: BudgetGuard):
        self.client = client
        self.guard = guard

    def complete(self, prompt: ScreenedPrompt, *, user_id: str, model: str,
                 tier: str = "free",
                 max_output_tokens: int = MAX_OUTPUT_TOKENS,
                 temperature: float = 0.7) -> Completion:
        if not isinstance(prompt, ScreenedPrompt):
            raise UnscreenedInput(
                f"ModelGateway.complete requires a ScreenedPrompt, got "
                f"{type(prompt).__name__}. Run safety.screen_input() first."
            )

        # Raises BudgetExceeded. Before the transport, deliberately.
        self.guard.check(
            model,
            estimate_prompt_tokens(prompt.system + prompt.user),
            max_output_tokens,
        )

        completion = self.client.generate(
            prompt,
            model=model,
            max_output_tokens=max_output_tokens,
            temperature=temperature,
        )

        spent = self.guard.record(
            user_id, completion.model,
            completion.prompt_tokens, completion.completion_tokens,
            tier=tier,
        )
        logger.info("model call: model=%s tier=%s tokens=%d/%d cost=$%.5f",
                    completion.model, tier, completion.prompt_tokens,
                    completion.completion_tokens, spent)

        return Completion(
            text=completion.text,
            model=completion.model,
            prompt_tokens=completion.prompt_tokens,
            completion_tokens=completion.completion_tokens,
            cost_usd=spent,
        )

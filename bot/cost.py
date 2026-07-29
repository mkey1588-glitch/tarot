"""
The monthly LLM budget guard.

Phase 0's entire budget is US$1-3K and a bug must not be able to touch it.
This module is what stands between a retry loop and the runway.

It is deliberately checked **before** the call, against the worst case that
call could cost, rather than after it against what it did cost. Noticing an
overrun afterwards is not a cap; it is a report. The consequence is that we
refuse slightly early — the guard assumes every call returns the full
`max_output_tokens` — and erring in that direction is the point.

The guard lives inside `bot/llm.py`'s single model choke point, not at its
call sites. A guard at a call site is one a new call site can skip.

Month-to-date is recomputed from the usage log on each call. That is O(n) in
calls made this month, which at 50-100 users is a few thousand short lines.
When that stops being true, the whole storage layer is being replaced anyway.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from bot.config import price_for
from bot.storage import JST

logger = logging.getLogger("uranai.cost")


class BudgetExceeded(RuntimeError):
    """Raised instead of making a call that would breach the monthly cap."""

    def __init__(self, spent_usd: float, budget_usd: float,
                 would_cost_usd: float):
        self.spent_usd = spent_usd
        self.budget_usd = budget_usd
        self.would_cost_usd = would_cost_usd
        super().__init__(
            f"monthly LLM budget reached: ${spent_usd:.4f} of "
            f"${budget_usd:.2f} spent, this call could cost up to "
            f"${would_cost_usd:.4f}. Refusing."
        )


def cost_usd(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    """Actual cost of a completed call."""
    input_rate, output_rate = price_for(model)
    return (prompt_tokens * input_rate + completion_tokens * output_rate) / 1e6


def estimate_prompt_tokens(text: str) -> int:
    """A deliberately pessimistic token estimate.

    One token per character overestimates for Japanese on current
    tokenizers. That is the safe direction: an overestimate refuses a call
    slightly early, an underestimate lets one through that should not have
    been. Not worth a tokenizer dependency to sharpen.
    """
    return len(text)


def worst_case_cost_usd(model: str, prompt_tokens: int,
                        max_output_tokens: int) -> float:
    """What this call costs if the model emits its full output allowance."""
    return cost_usd(model, prompt_tokens, max_output_tokens)


def _month_key(moment: datetime) -> str:
    """Billing month in JST. The same convention as the free-tier reset."""
    return moment.astimezone(JST).strftime("%Y-%m")


class BudgetGuard:
    def __init__(self, storage, budget_usd: float):
        self.storage = storage
        self.budget_usd = budget_usd

    def month_to_date_usd(self, now: Optional[datetime] = None) -> float:
        now = now or datetime.now(timezone.utc)
        current = _month_key(now)
        total = 0.0
        for record in self.storage.iter_llm_usage():
            try:
                stamped = datetime.fromisoformat(record["ts"])
            except (KeyError, ValueError):
                continue
            if stamped.tzinfo is None:
                stamped = stamped.replace(tzinfo=timezone.utc)
            if _month_key(stamped) != current:
                continue
            # Prefer the cost recorded at the time. Prices change, and a
            # historical call's cost is what it cost, not what it would
            # cost today.
            if "cost_usd" in record:
                total += float(record["cost_usd"])
                continue
            try:
                total += cost_usd(record.get("model", ""),
                                  int(record.get("prompt_tokens", 0)),
                                  int(record.get("completion_tokens", 0)))
            except Exception:
                continue
        return total

    def remaining_usd(self, now: Optional[datetime] = None) -> float:
        return max(0.0, self.budget_usd - self.month_to_date_usd(now))

    def check(self, model: str, prompt_tokens: int, max_output_tokens: int,
              now: Optional[datetime] = None) -> None:
        """Raise BudgetExceeded rather than make a call that would breach.

        Called before every model call, from inside the choke point.
        """
        spent = self.month_to_date_usd(now)
        would_cost = worst_case_cost_usd(model, prompt_tokens, max_output_tokens)

        if spent + would_cost > self.budget_usd:
            logger.error(
                "LLM budget guard refused a call: $%.4f of $%.2f spent this "
                "month, call could cost up to $%.4f (model=%s)",
                spent, self.budget_usd, would_cost, model,
            )
            self.storage.log_event("budget_refused", {
                "model": model,
                "spent_usd": round(spent, 6),
                "budget_usd": self.budget_usd,
                "would_cost_usd": round(would_cost, 6),
            })
            raise BudgetExceeded(spent, self.budget_usd, would_cost)

    def record(self, user_id: str, model: str, prompt_tokens: int,
               completion_tokens: int, **extra) -> float:
        """Log a completed call and return what it cost."""
        spent = cost_usd(model, prompt_tokens, completion_tokens)
        self.storage.log_llm_usage(user_id, {
            "model": model,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "cost_usd": round(spent, 8),
            **extra,
        })
        return spent

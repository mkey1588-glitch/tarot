"""
The payment seam.

Phase 0 tests a single ¥200-500 payment — not a subscription, which is
Phase 1. This module is the boundary that payment sits behind, and a stub
implementation that exercises the whole funnel without money moving.

WHY THERE IS NO STRIPE IMPLEMENTATION HERE YET
-----------------------------------------------
Charging for a reading is gated on things that are not code:

  * The prompts are placeholders written by an engineer (gate 2, P5).
    Taking ¥300 for that is not a pricing experiment, it is selling
    something we know is not the product.
  * 特定商取引法 requires a published notice the moment payment is enabled
    (gate 5). It is not optional and it is not ours to draft.
  * Rule 5 requires legal review of user-facing copy, which includes every
    word of a paywall (gate 6).

So `enabled_for` refuses unless `bot/readiness.py` says all six gates are
met. That is the same shape as the model choke point refusing an unscreened
prompt: the rule is enforced where it cannot be forgotten, rather than
written down somewhere and hoped for. Swapping StubProvider for a Stripe
implementation does not change it.

RULE 1 LIVES HERE TOO
---------------------
"Never monetise fear" is a constraint on the paywall, not only on the
reading. A paywall that says "misfortune is coming, pay to see the remedy"
is the exact shape the amended 消費者契約法 makes voidable. The offer copy
is in messages_ja.py and screened by the same test that screens every other
canned message.
"""

from __future__ import annotations

import logging
import secrets
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Optional

from bot import readiness

logger = logging.getLogger("uranai.payments")


class PaymentsNotPermitted(RuntimeError):
    """Raised when payment is attempted before the launch gates are met."""


@dataclass(frozen=True)
class Checkout:
    """A payment the user has been sent to complete."""

    checkout_id: str
    url: str
    amount_jpy: int
    provider: str


def blocking_gates(config=None) -> List[str]:
    """Which gates stand between us and being allowed to charge."""
    return [gate.key for gate in readiness.blocking(config)]


def enabled_for(config=None) -> bool:
    """True only when every launch gate is met.

    Deliberately all six rather than a payment-specific subset. A reading
    nobody has reviewed is not one we may sell, and the practitioner and
    counsel gates are exactly what "reviewed" means here.
    """
    return readiness.ready_for_real_users(config)


class PaymentProvider(ABC):
    """Anything that can take ¥200-500 from a user."""

    name = "abstract"

    @abstractmethod
    def create_checkout(self, user_id: str, amount_jpy: int,
                        description: str) -> Checkout:
        ...

    def _require_permission(self, config) -> None:
        if not enabled_for(config):
            gates = ", ".join(blocking_gates(config))
            logger.error("payment refused: gates not met (%s)", gates)
            raise PaymentsNotPermitted(
                "refusing to take payment: launch gates not met "
                f"({gates}). Charging for a reading written by an engineer, "
                "with no 特商法 notice and no legal review, is not a pricing "
                "experiment. See docs/DECISIONS.md and CLAUDE.md."
            )


class StubProvider(PaymentProvider):
    """Moves no money. Exercises the funnel end to end.

    The paywall, the checkout hand-off and the funnel events are all real;
    only the transfer is not. That is enough to test every path and to
    rehearse the flow with friends and family, and it cannot take anyone's
    money by accident because there is nothing behind it.
    """

    name = "stub"

    def __init__(self, config=None):
        self.config = config
        self.created: List[Checkout] = []

    def create_checkout(self, user_id: str, amount_jpy: int,
                        description: str) -> Checkout:
        # Not gated: a stub cannot charge anyone, and gating it would make
        # the funnel untestable until the practitioner is hired.
        checkout = Checkout(
            checkout_id=f"stub_{secrets.token_hex(8)}",
            url=f"https://example.invalid/checkout/stub_{secrets.token_hex(4)}",
            amount_jpy=amount_jpy,
            provider=self.name,
        )
        self.created.append(checkout)
        logger.info("stub checkout for %s: ¥%d (%s)", user_id, amount_jpy,
                    description)
        return checkout


class StripeProvider(PaymentProvider):
    """Stripe Checkout, in JPY.

    Refuses via `_require_permission` until every launch gate is met, so
    today this raises rather than charges. That is not a placeholder — it
    is the control, and it stays after the gates close.

    WHAT ¥300 BUYS
    --------------
    One paid-tier reading, redeemed by asking a question *after* paying —
    not a deeper answer to the question asked before. That is deliberate:
    holding the earlier question would mean storing what she wrote while
    she waited on a payment page, and we do not keep questions. A credit
    is a smaller thing to hold than a sentence about her marriage.

    JPY IS ZERO-DECIMAL
    -------------------
    ¥300 is `300`, not `30000`. Stripe's amounts are in the currency's
    smallest unit, and for most currencies that is 1/100 — so the habit of
    multiplying by a hundred, correct for USD, overcharges a Japanese
    customer a hundredfold. There is a test pinning this.
    """

    name = "stripe"

    def __init__(self, config):
        self.config = config

    def _client(self):
        try:
            import stripe
        except ImportError as exc:  # pragma: no cover
            raise PaymentsNotPermitted(
                "the stripe package is not installed") from exc
        stripe.api_key = self.config.stripe_secret_key
        return stripe

    def create_checkout(self, user_id: str, amount_jpy: int,
                        description: str) -> Checkout:
        self._require_permission(self.config)
        stripe = self._client()

        session = stripe.checkout.Session.create(
            mode="payment",
            line_items=[{
                "price_data": {
                    "currency": "jpy",
                    # Zero-decimal. See the class docstring.
                    "unit_amount": amount_jpy,
                    "product_data": {"name": description},
                },
                "quantity": 1,
            }],
            # Ties the completed payment back to a user without putting an
            # identifier in the URL the customer sees.
            client_reference_id=user_id,
            success_url=self.config.payment_success_url,
            cancel_url=self.config.payment_cancel_url,
            locale="ja",
        )
        return Checkout(checkout_id=session.id, url=session.url,
                        amount_jpy=amount_jpy, provider=self.name)

    def verify_webhook(self, body: bytes, signature: str):
        """Confirm Stripe really sent this. Returns the event, or None.

        Unverified, anyone who finds the endpoint can claim a payment
        completed and be handed a paid reading.
        """
        stripe = self._client()
        secret = self.config.stripe_webhook_secret
        if not secret:
            logger.error("STRIPE_WEBHOOK_SECRET is not set; refusing the event")
            return None
        try:
            return stripe.Webhook.construct_event(body, signature, secret)
        except Exception as exc:
            logger.warning("rejected a Stripe webhook: %s", type(exc).__name__)
            return None


def provider_for(config, force_stub: bool = True) -> PaymentProvider:
    """Pick a provider. Defaults to the stub, on purpose.

    `force_stub=False` asks for the real one, which will refuse unless the
    gates are met — so the caller cannot get a charging provider by wanting
    one badly enough.
    """
    if force_stub or not enabled_for(config):
        return StubProvider(config)
    return StripeProvider(config)

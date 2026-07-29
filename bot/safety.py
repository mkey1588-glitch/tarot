"""
Safety and compliance filter.

This module exists because of Section 12 of the board package. In this
category compliance constrains the product mechanics, not just the footer,
and three of the five non-negotiable rules are enforceable in code:

  Rule 1  Never monetise fear -- no output may imply that misfortune is
          coming and that payment averts it. Contracts induced by inducing
          fear of misfortune are voidable under the amended 消費者契約法.
  Rule 2  Disclose AI use clearly and permanently.
  Rule 3  No medical, legal, financial or life-or-death claims; route any
          sign of crisis to human help rather than to a reading.

The remaining two rules (spending caps, legal review of marketing copy)
live outside this module.

NOTHING HERE IS A SUBSTITUTE FOR THE LEGAL REVIEW budgeted in Phase 1.
It is a floor, not a ceiling, and the crisis resources below must be
confirmed as current before any real user sees them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


class Verdict(Enum):
    ALLOW = "allow"
    REDIRECT_CRISIS = "redirect_crisis"   # human help, never a reading
    REDIRECT_PROFESSIONAL = "redirect_professional"  # doctor/lawyer/adviser
    BLOCK = "block"


@dataclass(frozen=True)
class SafetyResult:
    verdict: Verdict
    reply: str | None = None
    reason: str | None = None

    @property
    def allowed(self) -> bool:
        return self.verdict is Verdict.ALLOW


# --- Inbound: what the user asks -------------------------------------------

# Crisis language. Deliberately broad: a false positive costs one reading,
# a false negative is unacceptable. Review with the retained practitioner
# and, before launch, with counsel.
_CRISIS_PATTERNS = [
    r"死にたい", r"消えたい", r"自殺", r"自死", r"リストカット", r"リスカ",
    r"生きていたくない", r"生きるのがつらい", r"いなくなりたい",
    r"死のうと", r"殺して", r"首を[つ吊]",
]

# Domains where a fortune-telling answer would be actively harmful.
_PROFESSIONAL_PATTERNS = {
    "medical": [r"癌", r"がん", r"腫瘍", r"余命", r"手術", r"薬を(やめ|止め)",
                r"病気は治", r"診断", r"うつ病", r"妊娠して(る|いる)"],
    "legal": [r"訴訟", r"裁判", r"離婚届", r"告訴", r"弁護士", r"相続"],
    "financial": [r"投資", r"株を", r"FX", r"仮想通貨", r"ビットコイン",
                  r"借金", r"融資", r"儲か"],
}

CRISIS_REPLY = (
    "お話ししてくださって、ありがとうございます。\n"
    "とてもつらい状況にいらっしゃるのだと感じました。\n\n"
    "占いではなく、いま話を聴いてくれる人につながってほしいと思っています。\n\n"
    "・こころの健康相談統一ダイヤル 0570-064-556\n"
    "・よりそいホットライン 0120-279-338\n\n"
    "どうか一人で抱え込まないでください。"
)
# TODO(legal): confirm both numbers are current and appropriate before launch.

PROFESSIONAL_REPLY = {
    "medical": (
        "健康や体調に関わることは、占いではお答えできません。\n"
        "医療機関や専門の相談窓口にご相談ください。\n\n"
        "そのうえで、いまのお気持ちについてなら一緒に考えられます。"
    ),
    "legal": (
        "法律に関わることは、占いではお答えできません。\n"
        "弁護士や法テラスなどの専門窓口にご相談ください。\n\n"
        "気持ちの整理という点であれば、お力になれるかもしれません。"
    ),
    "financial": (
        "投資や借入といったお金の判断は、占いではお答えできません。\n"
        "ファイナンシャルプランナーや公的な相談窓口にご相談ください。\n\n"
        "迷っているお気持ちについてなら、一緒に考えられます。"
    ),
}


def screen_input(text: str) -> SafetyResult:
    """Screen a user message before any model call.

    Crisis detection runs first and unconditionally: it must not be possible
    for a crisis message to reach the model.
    """
    for pattern in _CRISIS_PATTERNS:
        if re.search(pattern, text):
            return SafetyResult(Verdict.REDIRECT_CRISIS, CRISIS_REPLY,
                                f"crisis pattern: {pattern}")

    for domain, patterns in _PROFESSIONAL_PATTERNS.items():
        for pattern in patterns:
            if re.search(pattern, text):
                return SafetyResult(Verdict.REDIRECT_PROFESSIONAL,
                                    PROFESSIONAL_REPLY[domain],
                                    f"{domain} pattern: {pattern}")

    return SafetyResult(Verdict.ALLOW)


# --- Outbound: what the model says -----------------------------------------

# 景品表示法: no claims of certainty about a fortune-telling result.
_ABSOLUTE_CLAIMS = [
    r"必ず", r"絶対に", r"100[%％]", r"確実に", r"間違いなく",
    r"保証します", r"当たります",
]

# 消費者契約法 / 霊感商法: fear paired with a remedy is the prohibited shape.
_FEAR_TERMS = [r"祟り", r"呪い", r"悪霊", r"因縁", r"不幸が訪れ",
               r"災いが", r"このままでは危険", r"取り返しのつかない"]
_REMEDY_TERMS = [r"お祓い", r"祈祷", r"購入", r"申し込", r"課金",
                 r"有料", r"円で", r"解除できます"]


def screen_output(text: str) -> SafetyResult:
    """Screen a generated reading before it is sent.

    A block here is a prompt defect, not a user problem: log it, regenerate
    or fall back, and feed it to the weekly practitioner review.
    """
    for pattern in _ABSOLUTE_CLAIMS:
        if re.search(pattern, text):
            return SafetyResult(Verdict.BLOCK, None,
                                f"景品表示法 risk -- absolute claim: {pattern}")

    has_fear = any(re.search(p, text) for p in _FEAR_TERMS)
    has_remedy = any(re.search(p, text) for p in _REMEDY_TERMS)
    if has_fear and has_remedy:
        return SafetyResult(Verdict.BLOCK, None,
                            "霊感商法 shape -- misfortune paired with a paid remedy")
    if has_fear:
        return SafetyResult(Verdict.BLOCK, None,
                            "fear-inducing framing without remedy; still off-brand")

    return SafetyResult(Verdict.ALLOW)


# --- Disclosure ------------------------------------------------------------

AI_DISCLOSURE_SHORT = "※ この鑑定は AI が生成しています。"

AI_DISCLOSURE_FULL = (
    "この鑑定は AI が生成しています。\n"
    "娯楽・自己理解のためのものであり、医療・法律・投資の判断には使えません。\n"
    "結果の正確性を保証するものではありません。"
)


def with_disclosure(reading: str) -> str:
    """Append the standing AI disclosure. Never optional (Rule 2)."""
    return f"{reading}\n\n{AI_DISCLOSURE_SHORT}"

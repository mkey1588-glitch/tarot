"""
The six gates from CLAUDE.md, "Before anything reaches a real user", checked
by machine instead of by memory.

    1. Engine reviewed by the practitioner against their own charts
    2. Prompts written in Japanese by the practitioner, not translated
    3. AI disclosure visible and permanent
    4. Crisis and professional routing tested end to end, helpline numbers
       confirmed current
    5. 特定商取引法 notice published — required the moment payment is enabled
    6. Legal review complete

CLAUDE.md also draws the line these gates sit on:

    "Friends-and-family smoke testing is fine before all six.
     A public LINE account is not."

So there are two thresholds, not one, and this module reports both. A board
demo is friends-and-family. Seed users giving us their real birth data and
reading generated Japanese are not — they are the case the six gates exist
for, and `ready_for_real_users()` is what says so out loud rather than
leaving it to whoever is looking at the deploy that afternoon.

Nothing here can be satisfied by an engineer deciding it has been. Each
check reads the artefact that would have changed if the work were done.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from bot import prompts_ja
from bot.config import REPO_ROOT

DECISIONS = REPO_ROOT / "docs" / "DECISIONS.md"
SAFETY = REPO_ROOT / "bot" / "safety.py"
TOKUSHOHO = REPO_ROOT / "docs" / "TOKUSHOHO.md"


@dataclass(frozen=True)
class Gate:
    key: str
    title: str
    met: bool
    detail: str


def _pending_rulings() -> List[str]:
    """Practitioner questions still open in docs/DECISIONS.md."""
    if not DECISIONS.exists():
        return ["docs/DECISIONS.md is missing"]
    text = DECISIONS.read_text(encoding="utf-8")
    pending = []
    current = None
    for line in text.splitlines():
        heading = re.match(r"### (P\d+)\.", line)
        if heading:
            current = heading.group(1)
        elif current and "**Ruling:** _pending_" in line:
            pending.append(current)
            current = None
    return pending


def _helplines_confirmed() -> bool:
    """True once the TODO(legal) on the crisis numbers has been cleared."""
    if not SAFETY.exists():
        return False
    return "TODO(legal)" not in SAFETY.read_text(encoding="utf-8")


def _tokushoho_published() -> bool:
    """The notice exists and nobody left a TODO(legal) in it.

    Same shape as the helpline check: wired to the artefact that would have
    changed, not to a flag. A filled-in template that has not been through
    counsel is more dangerous than an empty one, because it looks reviewed.
    """
    if not TOKUSHOHO.exists():
        return False
    return "TODO(legal)" not in TOKUSHOHO.read_text(encoding="utf-8")


def _disclosure_is_structural() -> bool:
    """The disclosure has to be applied where a generation cannot skip it."""
    outbound = (REPO_ROOT / "bot" / "outbound.py")
    if not outbound.exists():
        return False
    source = outbound.read_text(encoding="utf-8")
    return "with_disclosure(model_text)" in source


def gates(config=None) -> List[Gate]:
    pending = _pending_rulings()
    payment_enabled = bool(config and getattr(config, "stripe_secret_key", None))
    legal_review = bool(config and getattr(config, "legal_review_completed_on",
                                           None))

    return [
        Gate(
            "engine_reviewed",
            "命式エンジンを実務家が自分の鑑定と照合",
            not pending,
            ("すべての判断が記録済み" if not pending else
             "未決: " + ", ".join(pending) +
             "（docs/DECISIONS.md — placeholder defaults are in force）"),
        ),
        Gate(
            "prompts_written",
            "プロンプトを実務家が日本語で執筆（翻訳ではなく）",
            not prompts_ja.PROMPTS_ARE_PLACEHOLDERS,
            ("実務家による原稿" if not prompts_ja.PROMPTS_ARE_PLACEHOLDERS
             else "現在はエンジニアが書いたプレースホルダー（P5）"),
        ),
        Gate(
            "ai_disclosure",
            "AI 利用の表示が常に付く",
            _disclosure_is_structural(),
            "outbound.reading() が必ず付与（生成側では省略できない）",
        ),
        Gate(
            "crisis_routing",
            "危機対応の導線と相談窓口番号の確認",
            _helplines_confirmed(),
            ("確認済み" if _helplines_confirmed() else
             "導線はテスト済み。番号は web で確認したのみで、"
             "法務レビュー未了（safety.py の TODO(legal)）"),
        ),
        Gate(
            "tokushoho",
            "特定商取引法に基づく表記",
            not payment_enabled or _tokushoho_published(),
            ("決済が無効のため現時点では不要" if not payment_enabled
             else "公開済み" if _tokushoho_published()
             else "決済が有効。docs/TOKUSHOHO.md に TODO(legal) が残っています"),
        ),
        Gate(
            "legal_review",
            "ユーザー向け文言の法務レビュー",
            legal_review,
            (f"完了: {getattr(config, 'legal_review_completed_on', '')}"
             if legal_review else "未実施（Rule 5）"),
        ),
    ]


def ready_for_real_users(config=None) -> bool:
    """All six. Seed users, a public LINE account, anything paid."""
    return all(gate.met for gate in gates(config))


def _crisis_routing_is_wired() -> bool:
    """The crisis path exists and runs ahead of any model call.

    Distinct from gate 4, which asks whether counsel has confirmed the
    numbers. This asks the narrower question of whether a message saying
    死にたい gets a helpline instead of a reading — which is a property of
    the code, and which bot/tests/test_no_bypass.py enforces.
    """
    safety = SAFETY.read_text(encoding="utf-8") if SAFETY.exists() else ""
    return ("_CRISIS_PATTERNS" in safety
            and "0120-279-338" in safety
            and "ScreeningToken(_MINT)" in safety)


def floor() -> List[Gate]:
    """What must hold before showing this to anyone at all.

    Not part of the six. CLAUDE.md says friends-and-family smoke testing is
    fine before all six gates, so nothing on that list blocks a board demo.
    These two are a lower floor that the six sit on top of: a board member
    forwards a link, and anyone at all might type 死にたい into a box.
    """
    return [
        Gate("disclosure_applied", "AI 表示が生成側で省略できない",
             _disclosure_is_structural(),
             "outbound.reading() が必ず付与"),
        Gate("crisis_wired", "危機的表現がモデルに届かない",
             _crisis_routing_is_wired(),
             "screen_input() が先に判定し、24時間無料の窓口を案内"),
    ]


def ready_for_friends_and_family(config=None) -> bool:
    """The board, and anyone else you would tell it is a prototype."""
    return all(gate.met for gate in floor())


def blocking(config=None) -> List[Gate]:
    return [gate for gate in gates(config) if not gate.met]


def summary(config=None) -> str:
    met = sum(1 for gate in gates(config) if gate.met)
    return f"{met}/{len(gates(config))} gates met"

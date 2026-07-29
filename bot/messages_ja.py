"""
Canned user-facing copy.

EVERY STRING IN THIS FILE IS A PLACEHOLDER.
==========================================
It exists so the pipeline has something to send while it is being built. It
is not the product's voice, and none of it has been through legal review.

  * The persona voice is P5 in docs/DECISIONS.md and belongs to the
    retained practitioner. It must be *written* in Japanese, not
    translated — translated copy is exactly what makes a Japanese user
    close the app, and avoiding that is a large part of what we are paying
    a practitioner for.
  * Rule 5 requires legal review of all user-facing copy before
    publication. None of this has had it.

WHY CANNED COPY IS A CLOSED SET
-------------------------------
`outbound.canned()` accepts a `Msg` member and nothing else, so there is no
way to send a user a string that has not been reviewed and registered here.
The alternative — a `send_text(str)` helper — is one refactor away from
someone passing it a fragment of model output.

Canned copy is screened by `screen_output` in the test suite rather than at
runtime, deliberately. Running the filter over our own reviewed text at
runtime would mean an edit to the crisis message could cause that message
to be *blocked*, which is the worst outcome this system has. Failing the
build instead is strictly better.

The crisis and professional replies live in `safety.py` and are referenced
here rather than copied, so there is one source of truth for each.
"""

from __future__ import annotations

from enum import Enum, unique
from typing import Dict

from bot.safety import AI_DISCLOSURE_FULL, CRISIS_REPLY, PROFESSIONAL_REPLY


@unique
class Msg(Enum):
    WELCOME = "welcome"
    HELP = "help"
    ASK_BIRTH_DATA = "ask_birth_data"
    BIRTH_DATA_SAVED = "birth_data_saved"
    BIRTH_DATA_UNPARSEABLE = "birth_data_unparseable"
    NEED_BIRTH_DATA_FIRST = "need_birth_data_first"
    QUOTA_EXHAUSTED = "quota_exhausted"
    MANUAL_REVIEW = "manual_review"
    READING_UNAVAILABLE = "reading_unavailable"
    SERVICE_PAUSED = "service_paused"
    CRISIS = "crisis"
    PROFESSIONAL_MEDICAL = "professional_medical"
    PROFESSIONAL_LEGAL = "professional_legal"
    PROFESSIONAL_FINANCIAL = "professional_financial"


# PLACEHOLDER — practitioner to rewrite. Do not ship.
TEMPLATES: Dict[Msg, str] = {

    # Onboarding is where AI use is disclosed at length. Rule 2 asks for
    # clear and permanent disclosure; the per-reading footer is the
    # permanent half and this is the clear half.
    Msg.WELCOME: (
        "はじめまして。四柱推命をもとにお話をうかがう占いです。\n\n"
        "生年月日をお送りいただくと、命式をお作りします。\n"
        "　例：1990-05-15\n"
        "出生時刻がわかる場合は、続けてお送りください。\n"
        "　例：1990-05-15 07:30\n\n"
        "時刻がわからなくても大丈夫です。その場合は三柱で拝見します。\n\n"
        "1日 {limit} 回まで無料でお試しいただけます。\n\n"
        + AI_DISCLOSURE_FULL
    ),

    # PLACEHOLDER — practitioner to rewrite. Do not ship.
    Msg.HELP: (
        "【使い方】\n"
        "・生年月日を送る（例：1990-05-15、または 1990-05-15 07:30）\n"
        "・「今日の運勢」で一日の傾向を見る\n"
        "・気になることをそのまま書いていただいても構いません\n"
        "・「ヘルプ」でこの画面\n\n"
        "1日 {limit} 回まで無料です。\n\n"
        + AI_DISCLOSURE_FULL
    ),

    # PLACEHOLDER — practitioner to rewrite. Do not ship.
    Msg.ASK_BIRTH_DATA: (
        "生年月日を教えていただけますか。\n"
        "　例：1990-05-15\n"
        "出生時刻がわかる場合は、続けてお書きください。\n"
        "　例：1990-05-15 07:30\n\n"
        "時刻がわからない場合は、日付だけで構いません。"
    ),

    # PLACEHOLDER — practitioner to rewrite. Do not ship.
    Msg.BIRTH_DATA_SAVED: (
        "ありがとうございます。{birth_summary} で承りました。\n\n"
        "{time_note}\n\n"
        "気になっていることを、そのままお書きください。"
    ),

    # PLACEHOLDER — practitioner to rewrite. Do not ship.
    Msg.BIRTH_DATA_UNPARSEABLE: (
        "うまく読み取れませんでした。\n"
        "次の形式でお送りいただけますか。\n"
        "　例：1990-05-15\n"
        "　例：1990-05-15 07:30"
    ),

    # PLACEHOLDER — practitioner to rewrite. Do not ship.
    Msg.NEED_BIRTH_DATA_FIRST: (
        "命式をお作りするために、先に生年月日を教えていただけますか。\n"
        "　例：1990-05-15"
    ),

    # Rule 4 and Rule 1 both bear on this one. It states the limit and when
    # it resets, and it does not imply that anything is lost by waiting.
    # PLACEHOLDER — practitioner to rewrite. Do not ship.
    Msg.QUOTA_EXHAUSTED: (
        "本日分の無料鑑定はここまでとなります。\n"
        "日本時間の午前0時にまたお使いいただけます。"
    ),

    # Not an apology, and not a generated reading. The chart genuinely sits
    # on a boundary the computation cannot resolve, so a person looks at it.
    # PLACEHOLDER — practitioner to rewrite. Do not ship.
    Msg.MANUAL_REVIEW: (
        "お預かりした生年月日は、暦のうえで節の切り替わりにとても近い時刻に"
        "あたります。\n"
        "この場合、命式が二通りに分かれることがあり、自動ではお出ししない"
        "ようにしています。\n\n"
        "担当者が確認してからあらためてご連絡します。少しお時間をください。\n"
        "（受付番号：{review_id}）"
    ),

    # Shown when screen_output blocked the generated reading. The user is
    # told nothing about why, and is not blamed. A block is a prompt defect
    # and it goes to the practitioner review, not to the user.
    # PLACEHOLDER — practitioner to rewrite. Do not ship.
    Msg.READING_UNAVAILABLE: (
        "申し訳ありません。今回はお出しできる鑑定文をご用意できませんでした。\n"
        "もう一度お試しいただけますか。"
    ),

    # PLACEHOLDER — practitioner to rewrite. Do not ship.
    Msg.SERVICE_PAUSED: (
        "ただいま混み合っており、鑑定を一時的にお休みしています。\n"
        "時間をおいてからお試しください。"
    ),

    # Referenced, not copied: safety.py is the source of truth for these.
    Msg.CRISIS: CRISIS_REPLY,
    Msg.PROFESSIONAL_MEDICAL: PROFESSIONAL_REPLY["medical"],
    Msg.PROFESSIONAL_LEGAL: PROFESSIONAL_REPLY["legal"],
    Msg.PROFESSIONAL_FINANCIAL: PROFESSIONAL_REPLY["financial"],
}

PROFESSIONAL_MESSAGE = {
    "medical": Msg.PROFESSIONAL_MEDICAL,
    "legal": Msg.PROFESSIONAL_LEGAL,
    "financial": Msg.PROFESSIONAL_FINANCIAL,
}

# Messages that must never have anything appended to them.
#
# The crisis reply is the whole reason this set exists. Someone who has just
# typed 死にたい should not then read a paragraph about automated screening:
# it makes a message that needs to be warm feel procedural, and "we detected
# this" reads as surveillance, which adds shame at the exact moment we are
# trying to remove it. Disclosure of AI use belongs in onboarding and in the
# privacy policy, both of which this user has already seen.
NEVER_APPEND = frozenset({
    Msg.CRISIS,
    Msg.PROFESSIONAL_MEDICAL,
    Msg.PROFESSIONAL_LEGAL,
    Msg.PROFESSIONAL_FINANCIAL,
})

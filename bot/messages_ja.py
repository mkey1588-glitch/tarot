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
    PAYWALL_OFFER = "paywall_offer"
    CHECKOUT_HANDOFF = "checkout_handoff"
    PAYMENT_UNAVAILABLE = "payment_unavailable"
    MANUAL_REVIEW = "manual_review"
    READING_UNAVAILABLE = "reading_unavailable"
    SERVICE_PAUSED = "service_paused"
    DATA_SUMMARY = "data_summary"
    DATA_NONE = "data_none"
    DATA_DELETE_CONFIRM = "data_delete_confirm"
    DATA_DELETED = "data_deleted"
    PAYMENT_RECEIVED = "payment_received"
    OPERATOR_REVIEW_ALERT = "operator_review_alert"
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
        "はじめまして。四柱推命でお話をうかがっています。\n\n"
        "気になっていることを、そのまま書いていただいて構いません。\n"
        "うまく言葉にならないことでも大丈夫です。\n\n"
        "はじめに、生年月日を教えていただけますか。\n"
        "　例：1990-05-15\n"
        "出生時刻がわかれば、続けてお書きください。\n"
        "　例：1990-05-15 07:30\n\n"
        "時刻はご存じない方が多いので、わからなくても大丈夫です。\n\n"
        "1日 {limit} 回まで無料です。\n\n"
        + AI_DISCLOSURE_FULL
    ),

    # PLACEHOLDER — practitioner to rewrite. Do not ship.
    Msg.HELP: (
        "【使い方】\n"
        "・生年月日を送る（例：1990-05-15、または 1990-05-15 07:30）\n"
        "・「今日の運勢」で一日の傾向を見る\n"
        "・気になることをそのまま書いていただいても構いません\n"
        "・「ヘルプ」でこの画面\n"
        "・「データ確認」でお預かりしている内容、「データ削除」で削除\n\n"
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
        "ありがとうございます。{birth_summary} で承りました。\n"
        "{time_note}\n\n"
        "それでは、気になっていることをお聞かせください。\n"
        "どなたかとの関わりのことでも、この先のことでも構いません。"
    ),

    # PLACEHOLDER — practitioner to rewrite. Do not ship.
    Msg.BIRTH_DATA_UNPARSEABLE: (
        "すみません、こちらでうまく読み取れませんでした。\n"
        "次のような形でお送りいただけると助かります。\n"
        "　例：1990-05-15\n"
        "　例：1990年5月15日 7時30分"
    ),

    # PLACEHOLDER — practitioner to rewrite. Do not ship.
    Msg.NEED_BIRTH_DATA_FIRST: (
        "お答えする前に、生年月日を教えていただけますか。\n"
        "命式をお作りするのに必要になります。\n"
        "　例：1990-05-15\n\n"
        "出生時刻はわからなくても大丈夫です。"
    ),

    # Rule 4 and Rule 1 both bear on this one. It states the limit and when
    # it resets, and it does not imply that anything is lost by waiting.
    # PLACEHOLDER — practitioner to rewrite. Do not ship.
    Msg.QUOTA_EXHAUSTED: (
        "本日分はここまでとさせてください。\n"
        "日付が変わりましたら、またお話をうかがえます。\n\n"
        "急いで決めなくてよいことのほうが、多いように思います。"
    ),

    # RULE 1 IS THE WHOLE DESIGN OF THIS MESSAGE.
    #
    # "Never monetise fear" constrains the paywall more than it constrains
    # any reading. The prohibited shape is: imply misfortune is coming, then
    # offer to avert it for money — which the amended 消費者契約法 makes
    # voidable, and which is why we chose subscription over pay-per-reading
    # as the eventual model.
    #
    # So this offer says what is included and what it costs. It does not
    # hint that something is wrong, does not imply the free reading withheld
    # anything worrying, and does not use scarcity or a deadline (Rule 4,
    # no dark patterns). If a future version of this copy needs the user to
    # feel uneasy to work, the product is wrong, not the copy.
    #
    # PLACEHOLDER — practitioner to rewrite. Do not ship.
    Msg.PAYWALL_OFFER: (
        "ここまでが無料でご覧いただける範囲です。\n\n"
        "もう少し詳しい鑑定をご希望でしたら、{price}円でご用意しています。\n"
        "・命式全体を踏まえた、長めの鑑定文\n"
        "・いまのご相談に絞った見立て\n\n"
        "お急ぎでなければ、日を改めてでも構いません。"
        "無料の鑑定は日本時間の午前0時にまたお使いいただけます。\n\n"
        "ご希望の場合は「詳しく」とお送りください。"
    ),

    # PLACEHOLDER — practitioner to rewrite. Do not ship.
    Msg.CHECKOUT_HANDOFF: (
        "お手続きのページをご用意しました。\n{url}\n\n"
        "{price}円（税込）・1回分の鑑定です。\n"
        "お支払い後、あらためて鑑定文をお送りします。"
    ),

    # Shown when the paywall is reached but payment is not permitted yet.
    # Honest about why: we are not ready to sell, rather than the user
    # having done something wrong.
    # PLACEHOLDER — practitioner to rewrite. Do not ship.
    Msg.PAYMENT_UNAVAILABLE: (
        "申し訳ありません。有料の鑑定は現在ご用意できていません。\n"
        "準備が整いましたらあらためてご案内します。"
    ),

    # Not an apology, and not a generated reading. The chart genuinely sits
    # on a boundary the computation cannot resolve, so a person looks at it.
    # PLACEHOLDER — practitioner to rewrite. Do not ship.
    Msg.MANUAL_REVIEW: (
        "お預かりした生年月日は、暦のうえで季節の変わり目にとても近い時刻に"
        "あたります。\n"
        "こうした場合、命式が二通りに分かれることがあります。"
        "確かでないまま申し上げたくないので、人が確かめてからお返事しています。\n\n"
        "少しお時間をいただきますが、あらためてご連絡します。\n"
        "（受付番号：{review_id}）"
    ),

    # Shown when screen_output blocked the generated reading. The user is
    # told nothing about why, and is not blamed. A block is a prompt defect
    # and it goes to the practitioner review, not to the user.
    # PLACEHOLDER — practitioner to rewrite. Do not ship.
    Msg.READING_UNAVAILABLE: (
        "すみません、今回はお出しできる形にまとまりませんでした。\n"
        "こちらの都合ですので、よろしければもう一度お尋ねください。"
    ),

    # PLACEHOLDER — practitioner to rewrite. Do not ship.
    Msg.SERVICE_PAUSED: (
        "ただいま混み合っており、鑑定を一時的にお休みしています。\n"
        "時間をおいてからお試しください。"
    ),

    # After payment. Says what she now has and invites the question — she
    # has not been asked for it yet, because we did not keep the one she
    # asked before paying.
    # PLACEHOLDER — practitioner to rewrite. Do not ship.
    Msg.PAYMENT_RECEIVED: (
        "ありがとうございます。お支払いを確認しました。\n\n"
        "それでは、あらためてお聞かせください。\n"
        "いま気にかかっていることを、そのままお書きいただければ、"
        "命式全体を踏まえてお返事します。"
    ),

    # 個人情報保護法 gives a person the right to know what is held about
    # them (開示) and to have it erased (削除). These are statutory rights,
    # not features, so the wording states plainly what we hold rather than
    # summarising it flatteringly.
    # PLACEHOLDER — practitioner to rewrite. Do not ship.
    Msg.DATA_SUMMARY: (
        "お預かりしている内容です。\n\n"
        "・生年月日：{birth}\n"
        "・鑑定の回数：{readings} 回\n"
        "・登録日：{created}\n\n"
        "削除をご希望の場合は「データ削除」とお送りください。"
    ),

    # PLACEHOLDER — practitioner to rewrite. Do not ship.
    Msg.DATA_NONE: (
        "現在お預かりしている情報はありません。\n"
        "生年月日をお送りいただくと、命式をお作りします。"
    ),

    # One confirmation, because erasure cannot be undone. Not two, and not a
    # buried link: making deletion hard is the dark pattern Rule 4 forbids,
    # and making it accidental is its own kind of harm.
    # PLACEHOLDER — practitioner to rewrite. Do not ship.
    Msg.DATA_DELETE_CONFIRM: (
        "お預かりしている生年月日と登録内容をすべて削除します。\n"
        "元に戻すことはできません。\n\n"
        "よろしければ「削除する」とお送りください。\n"
        "やめる場合は、そのまま別のことをお送りいただければ大丈夫です。"
    ),

    # PLACEHOLDER — practitioner to rewrite. Do not ship.
    Msg.DATA_DELETED: (
        "削除しました。生年月日と登録内容はもう保持していません。\n\n"
        "ご利用いただいた回数などの集計は、どなたのものか分からない形にして"
        "残しています。個人を特定できる情報は含まれていません。\n\n"
        "またご利用になる場合は、生年月日をお送りください。"
    ),

    # Operational, not product copy — it goes to us, not to a customer, so
    # it does not need the practitioner's voice. It does need the same
    # discipline about personal information: the review id and nothing else.
    # The birth data is in the queue entry, which the reviewer opens; a
    # phone notification is not where it belongs.
    Msg.OPERATOR_REVIEW_ALERT: (
        "【要確認】節気の境界にあたる命式が1件あります。\n"
        "受付番号：{review_id}\n"
        "/admin/review-queue で内容を確認してください。"
    ),

    # Referenced, not copied: safety.py is the source of truth for these.
    Msg.CRISIS: CRISIS_REPLY,
    Msg.PROFESSIONAL_MEDICAL: PROFESSIONAL_REPLY["medical"],
    Msg.PROFESSIONAL_LEGAL: PROFESSIONAL_REPLY["legal"],
    Msg.PROFESSIONAL_FINANCIAL: PROFESSIONAL_REPLY["financial"],
}

# Quick-reply buttons. Labels are user-facing copy and are screened by the
# same test as everything else here — a button is smaller than a paragraph
# and correspondingly easier to forget is copy at all.
#
# PLACEHOLDER — practitioner to rewrite. Do not ship.
def _q(label, kind="message", payload=""):
    from bot.outbound import QuickAction
    return QuickAction(label, kind, payload or label)


def quick(name: str):
    """Named button sets. Built lazily to avoid a circular import."""
    sets = {
        # Offered with the welcome and after registration: the two things
        # she is most likely to want, plus the way out.
        "start": [_q("生年月日を選ぶ", "date"), _q("ヘルプ")],
        "after_reading": [_q("今日の運勢"), _q("ヘルプ")],
        "after_register": [_q("今日の運勢"), _q("ヘルプ")],
        # Never attached to a crisis or professional referral. Those are not
        # moments to offer someone a menu.
        "data": [_q("データ確認"), _q("データ削除")],
    }
    return sets[name]


QUICK_LABELS = ["生年月日を選ぶ", "ヘルプ", "今日の運勢",
                "データ確認", "データ削除"]

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

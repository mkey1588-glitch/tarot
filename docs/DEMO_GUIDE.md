# The demo — a guide for the board and the cofounder

You have been sent a link and an access code. This explains what you are
looking at, what is real, and — the part that matters most — what you should
not conclude from it.

Five minutes, in the order below, is enough.

---

## First: what this is

A window onto the reading pipeline. It is **not** the product.

The product is a LINE bot. This page exists because the pipeline is easier to
judge when the birth chart, the exact instruction sent to the AI, and the
reply are on one screen — which a chat window cannot show you.

It runs the **real** system. Same chart engine, same safety screening, same
disclosure. Nothing on the page is a mock-up or a screenshot.

---

## Getting in

Open the link, enter the code you were given, press **入る**.

The code exists because the page asks for a birth date, and a birth date is
personal information under 個人情報保護法. An unlisted URL is not access
control.

**Nothing you type is stored.** The birth date is used to compute a chart and
then discarded. Your question is never saved — not even when the crisis
filter fires, where we record only which pattern matched and the time.
`個人情報の扱い` at the top of the page says this in Japanese.

---

## The five-minute tour

Use the grey buttons along the top of the left panel. Each is one scenario.

### 1. 四柱（時刻あり） — a normal reading

Press it, then **鑑定する**.

- **Left of the reply**: what the user sees in LINE.
- **Below it**: what happened, stage by stage.
- **Bottom**: the 命式 the engine computed.

Two things to look at. The 命式 table is computed by our own code, not by the
AI — **the AI never calculates a chart**, it only interprets one it is given.
And the reply ends with 「※ この鑑定は AI が生成しています。」, which is
attached by the system and cannot be omitted by a generation.

Open **モデルに渡した内容を全部見る** to see the exact instruction sent to
the AI. Note there is no birth date in it, only the finished chart.

### 2. 三柱（時刻なし） — when the birth time is unknown

Many people do not know what time they were born. Rather than turn them away
at the first question, the chart is built from three pillars instead of four,
the 時柱 column reads 不明, and the AI is explicitly told not to comment on
anything the missing pillar would have carried.

This matters commercially: requiring a birth time would put a wall at the
first question, and a user who was never asked cannot be told apart from one
who declined to pay.

### 3. 節気の境界 → 有人対応 — the chart we refuse to answer

Some births fall so close to a seasonal boundary that the chart could
legitimately be read two ways. The system does not guess. The user is told a
person will check, with a reference number, and the case goes to a queue.

This is the behaviour that protects us from a knowledgeable customer catching
us being confidently wrong.

### 4. 危機的な表現 — the one to look at hardest

The question is 「もう死にたいです」.

Watch the pipeline panel: every stage after the first reads **not called**.
The message never reaches the AI at all. The user gets consultation
helplines, with the 24-hour free line first.

No AI disclosure is appended here, deliberately. Someone in that state should
not then read a paragraph about automated screening.

### 5. 医療に関する質問 — out of scope by design

「癌は治りますか」 is referred to a medical professional. Same for legal and
financial questions. A fortune-telling answer to those would be actively
harmful, and is illegal to give in some forms.

### 6. 未登録 — before any birth data

Simply asks for a birth date. Included so you can see there is no path that
produces a reading without a chart.

---

## What you should NOT conclude from this

**The writing is not the product's writing.** Every Japanese sentence you
read was written by an engineer as scaffolding. The actual reading text will
be written by the practising fortune teller we are retaining, in Japanese,
from scratch — not translated. The red **DEMO** banner says this on every
page.

This is the single most likely thing to mislead you. What you are judging is
whether the machinery is sound: the chart is right, the dangerous questions
are stopped, the disclosure is attached, the cost is capped. Whether the
reading is *good* is a question this demo cannot answer, and it is the
question the practitioner is being hired to answer.

**The AI is not connected.** The demo runs a fixed stub reply so it cannot
spend money. The pipeline around it is real; the text is a placeholder that
says so.

**公開準備状況** in the banner shows six launch gates, of which four are
open — no practitioner, no legal review. The page will tell you it is not
ready for real users, because it is not.

---

## What to look at if you have longer

- **公開準備状況** — the six gates, checked automatically rather than
  remembered. It reads `docs/DECISIONS.md` and the source, so it cannot be
  ticked off by someone deciding it is done.
- **モデルの生の出力（検査前）** on a reading — what the AI produced before
  screening. Try preset 7 in the local version to see a reading being blocked
  for making an absolute claim.
- The footer counts charts computed, how many lacked a birth time, and spend
  against the monthly cap.

---

## Questions this demo is meant to provoke

1. Is ¥200–500 the right price for what a paid reading would contain?
2. Is refusing a boundary chart the right trade, given it costs us a
   conversion each time?
3. How many free readings before the paywall? It is three per day now, which
   is a guess.
4. Is the crisis response the one we want, and who signs it off?

The fourth is the one I would want answered first.

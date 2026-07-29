# bot/ — Phase 0 LINE bot

Ported from the "AI Uranai Board Package v1.1" prototype. What carried over,
what did not, and what is still missing.

## Carried over

- **The overall shape.** FastAPI + LINE Messaging API webhook, JSON-file
  storage, per-user free quota, cost logging. Right-sized for 50–100 users.
- **The validation method.** Free deep reading to seed behaviour, then a
  ¥200–500 paywall on a segment to measure conversion. This is the whole
  point of Phase 0.
- **Most of the Japanese system prompt.** Its constraint list — no absolutes,
  hedged phrasing, no medical or legal advice, route serious distress to
  support — was already close to what compliance requires.

## Replaced

- **Western sun signs → `engine/`.** The prototype computed a zodiac sign
  from a birthday. Japanese users expect 四柱推命 and will judge the product
  on chart accuracy. See `chart_service.py`.
- **Advisory constraints → enforced ones.** The prototype asked the model
  not to make absolute claims. `safety.py` checks that it did not, on the way
  in and on the way out. A prompt is a request; a filter is a guarantee.

## Still to do

- [ ] `app.py` — webhook, signature verification, event routing. Port from
      the prototype, inserting `screen_input` before any model call and
      `screen_output` + `with_disclosure` before any reply.
- [ ] `storage.py` — port as-is. Remember `data/` is gitignored: birth data
      is personal information under 個人情報保護法.
- [ ] `prompts_ja.py` — **blocked on the retained practitioner.** Written in
      Japanese, not translated. See P5 in `docs/DECISIONS.md`.
- [ ] Stripe checkout for the single reading.
- [ ] 特定商取引法 notice — required the moment payment is enabled.
- [ ] `ManualReviewRequired` handling: a boundary chart must reach a human,
      not a generated apology.

## The call path, once wired

```
inbound message
  └─ screen_input()          crisis → helpline, never a reading
                             medical/legal/financial → professional referral
  └─ build_payload()         deterministic chart; raises ManualReviewRequired
  └─ format_for_prompt()     chart as text the practitioner can audit
  └─ model call              interpretation only — it never calculates
  └─ screen_output()         block on 景品表示法 / 霊感商法 patterns
  └─ with_disclosure()       Rule 2, not optional
  └─ reply
```

Both screens are mandatory. If a code path skips one, that is a defect, and
the tests in `tests/test_safety.py` are what stop it drifting.

## Cost

The prototype measured well under $5/month at 1,000 MAU on a small model.
Keep `MONTHLY_LLM_BUDGET_USD` set — Phase 0 total budget is $1–3K and a
runaway loop should not be able to touch it.

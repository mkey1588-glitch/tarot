# Decisions log

Two kinds of decision are tracked here.

**Practitioner decisions** are questions where Four Pillars schools genuinely
disagree. They are not engineering choices and an engineer picking one is a
bug, not a shortcut. The code exposes each as an explicit option with a
placeholder default; this file records what the retained reader rules and why.

**Engineering decisions** are ours, recorded so the reasoning survives.

---

## Practitioner decisions — OPEN, blocked on the retainer

### P1. Does the day pillar change at 23:00 or at midnight? (早子時 / 晩子時)

- **Code:** `ChartOptions.day_changes_at_2300`
- **Placeholder default:** `True` (rolls forward at 23:00)
- **Why it matters:** affects every birth between 23:00 and 23:59 — roughly
  4% of users. The day pillar carries the 日主, so getting this wrong
  changes the core of the reading, not a detail.
- **Ruling:** _pending_

### P2. Do we apply local mean time correction? (地方時修正)

- **Code:** `ChartOptions.apply_local_mean_time`, `birth_longitude_deg`
- **Placeholder default:** `False`
- **Why it matters:** JST is fixed to 135°E (Akashi). A birth in Fukuoka
  (130.4°E) is ~18 minutes off local solar time; Sapporo (141.3°E) ~25
  minutes the other way. Enough to move an hour pillar. Some Japanese
  schools correct, others use clock time as reported.
- **Product cost if yes:** we must ask for birth *place*, not just time,
  which adds an onboarding field. Worth knowing before we design the flow.
- **Ruling:** _pending_

### P3. Which set of hidden stems? (蔵干)

- **Code:** `constants.BRANCH_HIDDEN_STEMS`
- **Placeholder default:** the common 三命通会 set
- **Why it matters:** schools differ on the minor hidden stems, which
  changes the strength reading of the chart.
- **Ruling:** _pending_

### P4. How close to a term boundary before we refuse to answer?

- **Code:** `ChartOptions.boundary_warning_minutes`
- **Placeholder default:** 30 minutes
- **Why it matters:** the solar computation is accurate to ~15 minutes, so
  30 is a safety factor of two. But the practitioner may want a wider window
  on principle — a birth 90 minutes from 立春 is computable but is exactly
  the kind of chart they would want to look at themselves.
- **Ruling:** _pending_

### P5. Persona voice

- **Code:** not yet written — this is the blocker on prompts
- **Why it matters:** Section 5.3 of the board package argues that localised
  emotional register is one of only three defensible positions we have. The
  target user is a woman aged 30–50 with a relationship question. Polite,
  indirect, warm, hedged rather than declarative, never alarming.
- **Note:** must be *written* in Japanese, not translated. Translation is
  what makes a product read like a chatbot.
- **Ruling:** _pending_

---

## Engineering decisions — settled

### E1. The model never calculates

Charts are computed in `engine/`, deterministic and unit-tested, and handed
to the model as structured data. A hallucinated chart is the one error a
knowledgeable user spots instantly; a chart bug is a failing test. Trading
the second class of error for the first is the whole point.

### E2. Pure standard library in the engine

No numpy, no ephemeris package, no network. The engine runs in CI without
credentials and the tests are the acceptance criteria for the practitioner
review. Cost: we implement solar longitude ourselves, accurate to ~15
minutes, which is why P4 exists.

### E3. Solar terms computed, not tabulated

A lookup table of official 暦要項 times would be exact, but would need
sourcing and maintaining per year. Computation is good enough everywhere
except within minutes of a boundary, and those cases are flagged rather
than guessed. Revisit if boundary charts turn out to be common enough to
matter commercially.

### E4. Safety filter is deny-by-default on crisis language

Crisis patterns are checked before any model call and cannot be bypassed.
Deliberately broad: a false positive costs one reading, a false negative is
unacceptable. Reviewed with counsel in Phase 1.

### E5. Outbound screening blocks rather than rewrites

If a generated reading trips 景品表示法 or 霊感商法 patterns, it is blocked
and logged rather than patched. A block is a prompt defect and should be
fixed in the prompt, where the practitioner can see it, not papered over at
the edge.

---

## How to use this file

When a practitioner ruling lands: record it here with the date and the
reasoning, change the default in `ChartOptions`, and add a test that pins
the behaviour. The test is what stops the decision quietly reverting.

#!/usr/bin/env python3
"""
Compare the engine against charts the practitioner computed by hand — gate 1.

    python3 scripts/practitioner_review.py docs/practitioner/charts.csv

Prints, for every chart, whether we agree, and for every disagreement which
open ruling would reconcile it. Most disagreements are not bugs: see the
module docstring in engine/review.py.

Needs no dependencies and no network. It runs on the system python3, so the
practitioner can be walked through it on their own machine.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine.review import load_csv, review, summarise   # noqa: E402

RULE = "─" * 72

HEADINGS = {
    "agrees": "一致",
    "P1": "P1（早子時/晩子時）で説明がつく",
    "P2": "P2（地方時修正）で説明がつく",
    "precision": "節気境界の精度の範囲内",
    "unexplained": "未説明 — エンジニアが見るべきもの",
}


def main(argv) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2

    path = Path(argv[1])
    if not path.exists():
        print(f"not found: {path}\n\nStart from "
              f"docs/practitioner/charts_template.csv")
        return 2

    expectations = load_csv(path)
    if not expectations:
        print(f"{path} has no chart rows.")
        return 2

    findings = review(expectations)

    print(f"\n{RULE}\n命式の照合 — {len(findings)} 件\n{RULE}")
    for finding in findings:
        mark = "○" if finding.agrees else "×"
        print(f"\n{mark} {finding.expectation.label}"
              f"   {finding.expectation.birth_local:%Y-%m-%d %H:%M}"
              f"{'' if finding.expectation.hour_known else ' （時刻不明・三柱）'}")
        if finding.expectation.note:
            print(f"    覚書: {finding.expectation.note}")
        if finding.agrees:
            continue
        for pillar in finding.mismatched:
            print(f"    {pillar:6} 実務家 {finding.expectation.expected[pillar]}"
                  f"   /   エンジン {finding.computed.get(pillar)}")
        print(f"    → {HEADINGS[finding.diagnosis]}")
        print(f"      {finding.detail}")

    counts = summarise(findings)
    print(f"\n{RULE}\n集計\n{RULE}")
    for key, heading in HEADINGS.items():
        if counts.get(key):
            print(f"  {counts[key]:3}  {heading}")

    unexplained = counts.get("unexplained", 0)
    print()
    if unexplained:
        print(f"  未説明が {unexplained} 件あります。"
              "エンジンの不具合か、まだ実装していない流派の作法です。")
    else:
        print("  未説明はありません。相違はすべて未決の論点で説明がつきます。")
    print("  P1 / P2 に分類された命式は、docs/DECISIONS.md の裁定の材料です。")
    print(RULE)
    return 1 if unexplained else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

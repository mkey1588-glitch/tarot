#!/usr/bin/env python3
"""
Fetch published solar-term times from the National Astronomical Observatory
of Japan, for use as independent test fixtures.

    python3 scripts/fetch_naoj_terms.py

Writes engine/tests/fixtures/naoj_solar_terms.json.

WHY THIS EXISTS
---------------
`engine/solar.py` claims accuracy of about 15 minutes. Before this, that
claim was pinned by two data points — 立春 for 2024 and 2025 — while the
target user is a woman aged 30-50, so born roughly 1976-1996. Worse,
`delta_t_seconds` uses different polynomial branches per era, and those
users fall in the 1961-1985 and 1986-2004 branches, neither of which had a
single real-world check. The engine was verified in a decade in which no
user was born.

These values are *independent*: computed by NAOJ, not by us. That is the
whole point, and it is the distinction CLAUDE.md draws when it says fixtures
captured from our own output are worthless as tests. Nothing here is derived
from `engine/`.

This script is run by hand and its output is checked in. The engine test
suite reads the JSON and never touches the network, because engine tests are
the acceptance criteria for the practitioner review and have to run in CI
with no credentials and no connectivity.

It is deliberately low-volume: a dozen requests, one second apart, against a
public government service.
"""

from __future__ import annotations

import json
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "engine" / "tests" / "fixtures" / "naoj_solar_terms.json"

ENDPOINT = "https://eco.mtk.nao.ac.jp/cgi-bin/koyomi/cande/phenomena_sy.cgi"

# Chosen to straddle every delta_t_seconds branch, weighted towards the years
# the target user was actually born in. 2024/2025 retain the coverage the
# suite already had.
YEARS = [1970, 1976, 1981, 1985,          # delta-T branch 1961-1985
         1986, 1990, 1996, 2000, 2004,    # delta-T branch 1986-2004
         2005, 2015, 2024, 2025]          # delta-T branch >= 2005

TERM_ROW = re.compile(
    r"(?P<name>[^\s(（]+)\s*[（(]黄経\s*(?P<degrees>\d+)\s*[°度]"
)


def fetch_year(year: int) -> dict:
    body = urllib.parse.urlencode({
        "year": year, "lst": 9, "lsti": 9,     # 9 = Japan Standard Time
        "phenom": 50,                          # 二十四節気 only
        "cal": 0, "jg": 2, "dtm": 0, "dt": 0,
        "body": 0, "coord": 1, "figure": 0,
    }).encode("ascii")

    request = urllib.request.Request(
        ENDPOINT, data=body,
        headers={"User-Agent": "uranai-engine-fixtures/1.0 (test fixtures)"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        html = response.read().decode("euc-jp", errors="replace")

    delta_t = None
    match = re.search(r"ΔＴ＝\s*([0-9.]+)", html) or re.search(r"ΔT[=＝]\s*([0-9.]+)", html)
    if match:
        delta_t = float(match.group(1))

    terms = []
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S):
        cells = [re.sub(r"<[^>]+>", "", c).strip().replace(" ", " ")
                 for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.S)]
        if len(cells) < 6 or "二十四節気" not in cells[3]:
            continue
        note = TERM_ROW.search(cells[5])
        if not note:
            continue
        date = datetime.strptime(f"{cells[0]} {cells[1]}", "%Y/%m/%d %H:%M")
        terms.append({
            "term": note.group("name"),
            "longitude_deg": int(note.group("degrees")),
            "jst": date.strftime("%Y-%m-%dT%H:%M"),
        })

    if len(terms) != 24:
        raise SystemExit(f"{year}: expected 24 terms, parsed {len(terms)}. "
                         "The page layout has probably changed — fix the "
                         "parser rather than accepting partial data.")
    return {"delta_t_seconds": delta_t, "terms": terms}


def main() -> None:
    years = {}
    for index, year in enumerate(YEARS):
        print(f"  {year} …", end="", flush=True)
        years[str(year)] = fetch_year(year)
        print(f" {len(years[str(year)]['terms'])} terms, "
              f"ΔT={years[str(year)]['delta_t_seconds']}s")
        if index < len(YEARS) - 1:
            time.sleep(1.0)   # a public service; do not hammer it

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "_source": "National Astronomical Observatory of Japan, "
                   "暦計算室 二十四節気・雑節 長期版",
        "_url": ENDPOINT,
        "_retrieved": datetime.now().strftime("%Y-%m-%d"),
        "_timezone": "JST (UT+9), as returned by the service",
        "_note": "Independently computed by NAOJ. Nothing here is derived "
                 "from engine/, which is what makes it a test rather than a "
                 "recording of our own output.",
        "_regenerate": "python3 scripts/fetch_naoj_terms.py",
        "years": years,
    }, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

    total = sum(len(v["terms"]) for v in years.values())
    print(f"\nwrote {OUT.relative_to(ROOT)}: {len(years)} years, {total} terms")


if __name__ == "__main__":
    sys.exit(main())

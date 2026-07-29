"""
Immutable reference data for Four Pillars (四柱推命).

Nothing in this module computes anything. It is the vocabulary that
engine.bazi speaks, kept separate so a practitioner can review it
without reading algorithm code.
"""

# 天干 — Heavenly Stems, index 0-9
STEMS = ["甲", "乙", "丙", "丁", "戊", "己", "庚", "辛", "壬", "癸"]
STEMS_KANA = ["きのえ", "きのと", "ひのえ", "ひのと", "つちのえ",
              "つちのと", "かのえ", "かのと", "みずのえ", "みずのと"]

# 地支 — Earthly Branches, index 0-11
BRANCHES = ["子", "丑", "寅", "卯", "辰", "巳", "午", "未", "申", "酉", "戌", "亥"]
BRANCHES_KANA = ["ね", "うし", "とら", "う", "たつ", "み",
                 "うま", "ひつじ", "さる", "とり", "いぬ", "い"]

# 五行 — Five Phases
WOOD, FIRE, EARTH, METAL, WATER = "木", "火", "土", "金", "水"

STEM_ELEMENT = [WOOD, WOOD, FIRE, FIRE, EARTH, EARTH, METAL, METAL, WATER, WATER]
# 陰陽 — True where the stem is 陽 (yang)
STEM_IS_YANG = [True, False, True, False, True, False, True, False, True, False]

BRANCH_ELEMENT = [WATER, EARTH, WOOD, WOOD, EARTH, FIRE,
                  FIRE, EARTH, METAL, METAL, EARTH, WATER]
BRANCH_IS_YANG = [True, False, True, False, True, False,
                  True, False, True, False, True, False]

# 蔵干 — Hidden stems within each branch, principal first.
# Schools differ on the minor hidden stems; this is the common 三命通会 set.
BRANCH_HIDDEN_STEMS = {
    "子": ["癸"],
    "丑": ["己", "癸", "辛"],
    "寅": ["甲", "丙", "戊"],
    "卯": ["乙"],
    "辰": ["戊", "乙", "癸"],
    "巳": ["丙", "戊", "庚"],
    "午": ["丁", "己"],
    "未": ["己", "丁", "乙"],
    "申": ["庚", "壬", "戊"],
    "酉": ["辛"],
    "戌": ["戊", "辛", "丁"],
    "亥": ["壬", "甲"],
}

# 節気 — The twelve sectional solar terms that open each Four Pillars month,
# with the sun's apparent ecliptic longitude at which each begins.
# 立春 (315 degrees) opens the year as well as the 寅 month.
SECTIONAL_TERMS = [
    ("立春", 315, "寅"),
    ("啓蟄", 345, "卯"),
    ("清明", 15,  "辰"),
    ("立夏", 45,  "巳"),
    ("芒種", 75,  "午"),
    ("小暑", 105, "未"),
    ("立秋", 135, "申"),
    ("白露", 165, "酉"),
    ("寒露", 195, "戌"),
    ("立冬", 225, "亥"),
    ("大雪", 255, "子"),
    ("小寒", 285, "丑"),
]

# Sequence number of each month branch counting 寅 = 1, used by the 五虎遁 rule.
MONTH_BRANCH_SEQUENCE = {
    "寅": 1, "卯": 2, "辰": 3, "巳": 4, "午": 5, "未": 6,
    "申": 7, "酉": 8, "戌": 9, "亥": 10, "子": 11, "丑": 12,
}

# Japan Standard Time is defined at 135 degrees East (Akashi).
JST_MERIDIAN_DEG = 135.0
JST_UTC_OFFSET_HOURS = 9

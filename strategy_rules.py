#!/usr/bin/env python3
"""
strategy_rules.py — 基于历史回测的多策略引擎
数据来源: titan_collector 742场完场比赛（首次出现>=6.5盘口）
更新: 2026-06-04（自动生成）

核心发现：
- 6球+80分后+赔率≥0.85 → EV+0.388, 89.5%≤7 (19场)
- 6球+80分后+赔率≥0.65 → EV+0.340, 88.9%≤7 (45场)
- 6球+80分后+赔率≥0.60 → EV+0.333, 89.7%≤7 (58场)
"""

def parse_ou_line(s):
    """解析盘口"""
    if not s:
        return 0
    try:
        s = str(s).strip()
        if '/' in s:
            parts = s.split('/')
            return (float(parts[0]) + float(parts[1])) / 2
        return float(s)
    except:
        return 0


# ============================================================
# 历史回测策略规则表（742场，样本≥10，按EV排序）
# ============================================================

STRATEGY_RULES = [
    {"id": "S1", "goals": 6, "minute_min": 80, "under_odds_min": 0.85,
     "ev": 0.388, "win_pct": 89.5, "sample": 19, "confidence": "MEDIUM",
     "note": "6球+80分后+赔率≥0.85"},
    {"id": "S2", "goals": 6, "minute_min": 80, "under_odds_min": 0.65,
     "ev": 0.34, "win_pct": 88.9, "sample": 45, "confidence": "HIGH",
     "note": "6球+80分后+赔率≥0.65"},
    {"id": "S3", "goals": 6, "minute_min": 80, "under_odds_min": 0.6,
     "ev": 0.333, "win_pct": 89.7, "sample": 58, "confidence": "HIGH",
     "note": "6球+80分后+赔率≥0.60"},
    {"id": "S4", "goals": 5, "minute_min": 60, "under_odds_min": 0.9,
     "ev": 0.294, "win_pct": 80.0, "sample": 35, "confidence": "HIGH",
     "note": "5球+60分后+赔率≥0.90"},
    {"id": "S5", "goals": 6, "minute_min": 75, "under_odds_min": 0.85,
     "ev": 0.288, "win_pct": 81.6, "sample": 49, "confidence": "HIGH",
     "note": "6球+75分后+赔率≥0.85"},
    {"id": "S6", "goals": 6, "minute_min": 70, "under_odds_min": 0.85,
     "ev": 0.266, "win_pct": 80.0, "sample": 70, "confidence": "HIGH",
     "note": "6球+70分后+赔率≥0.85"},
    {"id": "S7", "goals": 6, "minute_min": 70, "under_odds_min": 0.6,
     "ev": 0.225, "win_pct": 81.5, "sample": 135, "confidence": "HIGH",
     "note": "6球+70分后+赔率≥0.60"},
    {"id": "S8", "goals": 5, "minute_min": 70, "under_odds_min": 0.9,
     "ev": 0.218, "win_pct": 76.2, "sample": 21, "confidence": "MEDIUM",
     "note": "5球+70分后+赔率≥0.90"},
    {"id": "S9", "goals": 5, "minute_min": 70, "under_odds_min": 0.7,
     "ev": 0.209, "win_pct": 77.1, "sample": 35, "confidence": "HIGH",
     "note": "5球+70分后+赔率≥0.70"},
    {"id": "S10", "goals": 5, "minute_min": 70, "under_odds_min": 0.75,
     "ev": 0.191, "win_pct": 76.5, "sample": 34, "confidence": "HIGH",
     "note": "5球+70分后+赔率≥0.75"},
    {"id": "S11", "goals": 6, "minute_min": 65, "under_odds_min": 0.85,
     "ev": 0.188, "win_pct": 77.3, "sample": 88, "confidence": "HIGH",
     "note": "6球+65分后+赔率≥0.85"},
    {"id": "S12", "goals": 5, "minute_min": 60, "under_odds_min": 0.85,
     "ev": 0.183, "win_pct": 76.6, "sample": 47, "confidence": "HIGH",
     "note": "5球+60分后+赔率≥0.85"},
    {"id": "S13", "goals": 6, "minute_min": 65, "under_odds_min": 0.6,
     "ev": 0.17, "win_pct": 78.8, "sample": 160, "confidence": "HIGH",
     "note": "6球+65分后+赔率≥0.60"},
]


def ou_line_in_range(ou_line_str, low=6.5, high=7.5):
    """检查盘口是否在合理范围内（默认6.5-7.5）"""
    val = parse_ou_line(ou_line_str)
    return low <= val <= high


def evaluate_multi_strategy(match):
    """
    多策略评估
    match: {home_score, away_score, minute, ou_line, under_odds}
    返回: (should_bet, strategy_id, ev, win_pct, confidence, note)
    """
    goals = (match.get("home_score") or 0) + (match.get("away_score") or 0)
    minute = match.get("minute", -1)
    ou_line = match.get("ou_line", "")
    under_odds = match.get("under_odds", 0)

    if minute < 0 or not under_odds:
        return False, None, 0, 0, "NO_DATA", "数据不完整"

    # 盘口范围过滤
    ou_val = parse_ou_line(ou_line)
    if ou_val < 6.5 or ou_val > 7.5:
        return False, None, 0, 0, "LINE_OUT", f"盘{ou_val:.2f}不在6.5-7.5范围"

    # 6-0比分明确负EV，排除
    hs = match.get("home_score") or 0
    aw = match.get("away_score") or 0
    if goals == 6 and ((hs == 6 and aw == 0) or (hs == 0 and aw == 6)):
        return False, None, 0, 0, "BLOWOUT", f"{hs}-{aw}一边倒，避开"

    # 按EV从高到低匹配策略
    for rule in sorted(STRATEGY_RULES, key=lambda x: -x["ev"]):
        if "home_score" in rule:
            if match.get("home_score") != rule["home_score"]:
                continue
            if match.get("away_score") != rule["away_score"]:
                continue
        else:
            if goals != rule["goals"]:
                continue

        if minute < rule["minute_min"]:
            continue

        if "under_odds_min" in rule:
            if not under_odds or under_odds < rule["under_odds_min"]:
                continue

        return True, rule["id"], rule["ev"], rule["win_pct"], rule["confidence"], rule["note"]

    return False, None, 0, 0, "NO_MATCH", f"球{goals} 盘{ou_val:.2f} {minute}min 小{under_odds or 0:.3f}"


if __name__ == "__main__":
    # 测试
    test_cases = [
        {"home_score": 3, "away_score": 3, "minute": 82, "ou_line": "6.5", "under_odds": 0.85},
        {"home_score": 4, "away_score": 2, "minute": 81, "ou_line": "6.5", "under_odds": 0.70},
        {"home_score": 3, "away_score": 3, "minute": 72, "ou_line": "6.5", "under_odds": 0.65},
        {"home_score": 4, "away_score": 2, "minute": 55, "ou_line": "6.5", "under_odds": 0.80},
        {"home_score": 6, "away_score": 0, "minute": 80, "ou_line": "6.5", "under_odds": 0.70},
        {"home_score": 2, "away_score": 3, "minute": 75, "ou_line": "6.5", "under_odds": 0.92},
    ]
    for tc in test_cases:
        result = evaluate_multi_strategy(tc)
        score = f"{tc['home_score']}-{tc['away_score']}"
        print(f"{score} {tc['minute']}min 盘{tc['ou_line']} 小{tc['under_odds']} → {result}")

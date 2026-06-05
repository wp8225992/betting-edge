#!/usr/bin/env python3
"""
strategy_rules.py — 基于历史回测的多策略引擎
数据来源: titan_collector 12456场snapshot（盘口6.75-7.5）
更新: 2026-06-05（自动生成）
修复: 正确按盘口类型结算（7盘口走水/7.5盘口7球全赢/7.25盘口7球输半）

核心发现：
- 1-5比分+70分后 → EV+0.227, 75.0% (20场)
- 5球+65分后+赔率≥0.90 → EV+0.185, 61.5% (39场)
- 6球+80分后+赔率≥1.00 → EV+0.183, 63.9% (36场)
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


def calc_under_result(ou_line, final_total, under_odds):
    """根据实际盘口计算买小结果"""
    if ou_line in ('7', '7.0'):
        if final_total < 7: return under_odds
        elif final_total == 7: return 0
        else: return -1
    elif ou_line in ('7.5',):
        if final_total <= 7: return under_odds
        else: return -1
    elif ou_line in ('6.5', '6.5/7', '6.75'):
        if final_total <= 6: return under_odds
        elif final_total == 7: return -0.5
        else: return -1
    elif ou_line in ('7/7.5', '7.25'):
        if final_total <= 6: return under_odds
        elif final_total == 7: return -0.5
        else: return -1
    else:
        if final_total < 7: return under_odds
        elif final_total == 7: return 0
        else: return -1


# ============================================================
# 历史回测策略规则表（12456场，样本≥15，按EV排序）
# ============================================================

STRATEGY_RULES = [
    {"id": "S1", "home_score": 1, "away_score": 5, "minute_min": 70,
     "ev": 0.227, "win_pct": 75.0, "sample": 20, "confidence": "MEDIUM",
     "note": "1-5比分+70分后"},
    {"id": "S2", "goals": 5, "minute_min": 65, "under_odds_min": 0.9,
     "ev": 0.185, "win_pct": 61.5, "sample": 39, "confidence": "HIGH",
     "note": "5球+65分后+赔率≥0.90"},
    {"id": "S3", "goals": 6, "minute_min": 80, "under_odds_min": 1.0,
     "ev": 0.183, "win_pct": 63.9, "sample": 36, "confidence": "HIGH",
     "note": "6球+80分后+赔率≥1.00"},
    {"id": "S4", "home_score": 0, "away_score": 7, "minute_min": 70,
     "ev": 0.158, "win_pct": 66.7, "sample": 15, "confidence": "MEDIUM",
     "note": "0-7比分+70分后"},
    {"id": "S5", "goals": 5, "minute_min": 65, "under_odds_min": 0.7,
     "ev": 0.151, "win_pct": 62.2, "sample": 45, "confidence": "HIGH",
     "note": "5球+65分后+赔率≥0.70"},
    {"id": "S6", "goals": 5, "minute_min": 65, "under_odds_min": 0.8,
     "ev": 0.151, "win_pct": 62.2, "sample": 45, "confidence": "HIGH",
     "note": "5球+65分后+赔率≥0.80"},
    {"id": "S7", "goals": 5, "minute_min": 70, "under_odds_min": 0.9,
     "ev": 0.149, "win_pct": 58.3, "sample": 36, "confidence": "HIGH",
     "note": "5球+70分后+赔率≥0.90"},
    {"id": "S8", "goals": 5, "minute_min": 75, "under_odds_min": 0.9,
     "ev": 0.149, "win_pct": 58.3, "sample": 36, "confidence": "HIGH",
     "note": "5球+75分后+赔率≥0.90"},
    {"id": "S9", "goals": 5, "minute_min": 80, "under_odds_min": 0.9,
     "ev": 0.149, "win_pct": 58.3, "sample": 36, "confidence": "HIGH",
     "note": "5球+80分后+赔率≥0.90"},
    {"id": "S10", "home_score": 5, "away_score": 1, "minute_min": 70,
     "ev": 0.144, "win_pct": 74.2, "sample": 31, "confidence": "MEDIUM",
     "note": "5-1比分+70分后"},
    {"id": "S11", "goals": 5, "minute_min": 60, "under_odds_min": 1.0,
     "ev": 0.144, "win_pct": 62.1, "sample": 29, "confidence": "MEDIUM",
     "note": "5球+60分后+赔率≥1.00"},
    {"id": "S12", "goals": 5, "minute_min": 65, "under_odds_min": 1.0,
     "ev": 0.144, "win_pct": 60.0, "sample": 25, "confidence": "MEDIUM",
     "note": "5球+65分后+赔率≥1.00"},
    {"id": "S13", "goals": 5, "minute_min": 60, "under_odds_min": 0.9,
     "ev": 0.135, "win_pct": 60.8, "sample": 51, "confidence": "HIGH",
     "note": "5球+60分后+赔率≥0.90"},
    {"id": "S14", "goals": 5, "minute_min": 60, "under_odds_min": 0.8,
     "ev": 0.125, "win_pct": 62.1, "sample": 66, "confidence": "HIGH",
     "note": "5球+60分后+赔率≥0.80"},
    {"id": "S15", "goals": 5, "minute_min": 70, "under_odds_min": 0.7,
     "ev": 0.121, "win_pct": 58.5, "sample": 41, "confidence": "HIGH",
     "note": "5球+70分后+赔率≥0.70"},
]


def ou_line_in_range(ou_line_str, low=6.5, high=7.5):
    """检查盘口是否在合理范围内"""
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
    test_cases = [
        {"home_score": 3, "away_score": 3, "minute": 82, "ou_line": "7", "under_odds": 0.85},
        {"home_score": 4, "away_score": 2, "minute": 81, "ou_line": "7.5", "under_odds": 0.70},
        {"home_score": 3, "away_score": 3, "minute": 72, "ou_line": "7/7.5", "under_odds": 0.65},
        {"home_score": 4, "away_score": 2, "minute": 55, "ou_line": "7", "under_odds": 0.80},
        {"home_score": 2, "away_score": 3, "minute": 75, "ou_line": "6.75", "under_odds": 0.92},
    ]
    for tc in test_cases:
        result = evaluate_multi_strategy(tc)
        score = f"{tc['home_score']}-{tc['away_score']}"
        print(f"{score} {tc['minute']}min 盘{tc['ou_line']} 小{tc['under_odds']} → {result}")

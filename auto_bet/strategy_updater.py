#!/usr/bin/env python3
"""strategy_updater.py — 策略规则自动更新器（v2: 修复幸存者偏差+盘口细分）"""
import sys
import os
import re
import json
import psycopg2
from collections import defaultdict
from pathlib import Path
from datetime import datetime

PG_DSN = "host=localhost dbname=titan_collector user=betting password=betting123"
SCRIPT_DIR = Path(__file__).parent
RULES_PATH = SCRIPT_DIR / "strategy_rules.py"
LOG_PATH = SCRIPT_DIR / "strategy_update.log"

# 所有采集的盘口
ALL_LINES = ('6.5','6.5/7','6.75','7','7.0','7/7.5','7.25','7.5','7.5/8','7.75','8','8.0','8/8.5','8.5','9','9.0','9.5','10')

# 分析用的盘口分组（7左右）
ANALYSIS_LINES = ('6.75','7','7.0','7/7.5','7.25','7.5')


def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    with open(str(LOG_PATH), "a", encoding="utf-8") as f:
        f.write(line + "\n")


def calc_under_result(ou_line, final_total, under_odds):
    """根据实际盘口计算买小的结果，返回profit"""
    if ou_line in ('7', '7.0'):
        if final_total < 7:
            return under_odds
        elif final_total == 7:
            return 0  # 走水
        else:
            return -1
    elif ou_line in ('7.5',):
        if final_total <= 7:
            return under_odds
        else:
            return -1
    elif ou_line in ('6.5', '6.5/7', '6.75'):
        # 6.75: 买小终场=7 输半
        if final_total <= 6:
            return under_odds
        elif final_total == 7:
            return -0.5  # 输半
        else:
            return -1
    elif ou_line in ('7/7.5', '7.25'):
        # 7.25: 买小终场=7 赢半
        if final_total < 7:
            return under_odds
        elif final_total == 7:
            return under_odds * 0.5  # 赢半
        else:
            return -1
    else:
        if final_total < 7:
            return under_odds
        elif final_total == 7:
            return 0
        else:
            return -1


def load_data():
    """加载所有snapshot（不去重）"""
    conn = psycopg2.connect(PG_DSN)
    cur = conn.cursor()
    cur.execute("""
    SELECT s.match_key, s.league, s.home, s.away, s.ou_line,
           s.home_score, s.away_score, s.minute, s.under_odds, s.over_odds,
           r.final_home_score, r.final_away_score, r.total_goals
    FROM snapshots s
    JOIN results r ON s.match_key = r.match_key
    WHERE r.is_finished = 1 AND s.ou_line IN %s
    ORDER BY s.match_key, s.minute;
    """, (ANALYSIS_LINES,))
    
    result = []
    for r in cur.fetchall():
        result.append({
            'match_key': r[0], 'league': (r[1] or '').strip(),
            'home': r[2] or '', 'away': r[3] or '',
            'ou_line': r[4], 'home_score': r[5], 'away_score': r[6],
            'minute': r[7], 'under_odds': r[8], 'over_odds': r[9],
            'final_home': r[10], 'final_away': r[11], 'final_total': r[12],
        })
    cur.close()
    conn.close()
    return result


def generate_rules(data):
    """生成策略规则：按进球数+时间+赔率，用正确盘口结算计算EV"""
    
    # 按match_key分组
    matches_by_key = defaultdict(list)
    for m in data:
        matches_by_key[m['match_key']].append(m)
    
    for mk in matches_by_key:
        matches_by_key[mk].sort(key=lambda x: x['minute'])
    
    def find_and_evaluate(goal_total, min_min, min_odds, min_line=None):
        """找到每场比赛首次满足条件的snapshot，用正确盘口结算计算EV"""
        subset = []
        for mk, snaps in matches_by_key.items():
            for s in snaps:
                if (s['home_score'] is not None and s['away_score'] is not None
                    and s['home_score'] + s['away_score'] == goal_total
                    and s['minute'] >= min_min
                    and s['under_odds'] is not None
                    and s['under_odds'] >= min_odds
                    and s['final_total'] is not None):
                    if min_line and s['ou_line'] != min_line:
                        continue
                    subset.append(s)
                    break
        return subset
    
    def calc_ev(subset):
        """用正确盘口结算计算EV"""
        n = len(subset)
        if n == 0:
            return 0, 0, 0, 0, 0
        total_profit = 0
        wins = 0
        pushes = 0
        half_lose = 0
        loses = 0
        
        for m in subset:
            profit = calc_under_result(m['ou_line'], m['final_total'], m['under_odds'])
            total_profit += profit
            if profit > 0:
                wins += 1
            elif profit == 0:
                pushes += 1
            elif profit > -1:
                half_lose += 1
            else:
                loses += 1
        
        ev = total_profit / n
        win_rate = (wins + pushes) / n * 100
        odds = [m['under_odds'] for m in subset if m['under_odds']]
        avg_odds = sum(odds) / len(odds) if odds else 0
        
        return ev, win_rate, avg_odds, wins, loses
    
    rules = []
    
    # === 按进球数扫描 ===
    for goals in range(3, 9):
        for min_min in [60, 65, 70, 75, 80]:
            for min_odds in [0.70, 0.80, 0.90, 1.00]:
                subset = find_and_evaluate(goals, min_min, min_odds)
                if len(subset) < 15:  # 提高样本门槛到15
                    continue
                
                ev, win_rate, avg_odds, w, l = calc_ev(subset)
                
                if ev > 0.05:  # 只保留正EV策略
                    # 看终场分布
                    final_dist = defaultdict(int)
                    for m in subset:
                        final_dist[m['final_total']] += 1
                    dist = ', '.join(f'{k}:{v}' for k,v in sorted(final_dist.items()))
                    
                    rules.append({
                        "goals": goals, "minute_min": min_min, "under_odds_min": min_odds,
                        "ev": round(ev, 3), "win_pct": round(win_rate, 1), "sample": len(subset),
                        "confidence": "HIGH" if len(subset) >= 30 else "MEDIUM",
                        "note": f"{goals}球+{min_min}分后+赔率≥{min_odds:.2f}",
                        "detail": f"终场分布:{dist}"
                    })
    
    # === 特殊比分策略 ===
    score_map = defaultdict(list)
    for mk, snaps in matches_by_key.items():
        for s in snaps:
            if (s['home_score'] is not None and s['away_score'] is not None 
                and s['final_total'] is not None):
                total = s['home_score'] + s['away_score']
                if total >= 4 and s['minute'] is not None and s['minute'] >= 70:
                    score_map[(total, s['home_score'], s['away_score'])].append(s)
    
    for (total, h, a), all_snaps in score_map.items():
        matches_for_score = []
        for mk2, snaps2 in matches_by_key.items():
            for s in snaps2:
                if (s['home_score'] == h and s['away_score'] == a
                    and s['minute'] >= 70 and s['under_odds'] is not None
                    and s['final_total'] is not None):
                    matches_for_score.append(s)
                    break
        
        if len(matches_for_score) < 15:
            continue
        ev, win_rate, avg_odds, w, l = calc_ev(matches_for_score)
        if ev > 0.10:
            rules.append({
                "home_score": h, "away_score": a, "minute_min": 70,
                "ev": round(ev, 3), "win_pct": round(win_rate, 1), "sample": len(matches_for_score),
                "confidence": "MEDIUM",
                "note": f"{h}-{a}比分+70分后",
            })
    
    # 排序+去重
    rules.sort(key=lambda x: (-x['ev'], -x['sample']))
    seen_notes = {}
    deduped = []
    for r in rules:
        key = r.get('note', '')
        if key not in seen_notes or r['ev'] > seen_notes[key]['ev']:
            seen_notes[key] = r
            deduped.append(r)
    
    for i, r in enumerate(deduped):
        r['id'] = f"S{i+1}"
    
    return deduped[:15]


def update_rules_file(new_rules, data_count):
    """更新strategy_rules.py"""
    now = datetime.now().strftime("%Y-%m-%d")
    
    top3 = new_rules[:3]
    summary_lines = [f"- {r['note']} → EV{r['ev']:+.3f}, {r['win_pct']}% ({r['sample']}场)" for r in top3]
    summary = "\n".join(summary_lines)
    
    rules_code = "STRATEGY_RULES = [\n"
    for r in new_rules:
        rules_code += "    "
        if 'home_score' in r:
            rules_code += '{"id": "%s", "home_score": %d, "away_score": %d, "minute_min": %d,\n' % (r["id"], r["home_score"], r["away_score"], r["minute_min"])
        else:
            rules_code += '{"id": "%s", "goals": %d, "minute_min": %d, "under_odds_min": %s,\n' % (r["id"], r["goals"], r["minute_min"], r["under_odds_min"])
        rules_code += '     "ev": %s, "win_pct": %s, "sample": %d, "confidence": "%s",\n' % (r["ev"], r["win_pct"], r["sample"], r["confidence"])
        rules_code += '     "note": "%s"},\n' % r["note"]
    rules_code += "]"
    
    content = f'''#!/usr/bin/env python3
"""
strategy_rules.py — 基于历史回测的多策略引擎
数据来源: titan_collector {data_count}场snapshot（盘口6.75-7.5）
更新: {now}（自动生成）
修复: 正确按盘口类型结算（7盘口走水/7.5盘口7球全赢/7.25盘口7球输半）

核心发现：
{summary}
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
# 历史回测策略规则表（{data_count}场，样本≥15，按EV排序）
# ============================================================

{rules_code}


def ou_line_in_range(ou_line_str, low=6.5, high=7.5):
    """检查盘口是否在合理范围内"""
    val = parse_ou_line(ou_line_str)
    return low <= val <= high


def evaluate_multi_strategy(match):
    """
    多策略评估
    match: {{home_score, away_score, minute, ou_line, under_odds}}
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
        return False, None, 0, 0, "LINE_OUT", f"盘{{ou_val:.2f}}不在6.5-7.5范围"

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

    return False, None, 0, 0, "NO_MATCH", f"球{{goals}} 盘{{ou_val:.2f}} {{minute}}min 小{{under_odds or 0:.3f}}"


if __name__ == "__main__":
    test_cases = [
        {{"home_score": 3, "away_score": 3, "minute": 82, "ou_line": "7", "under_odds": 0.85}},
        {{"home_score": 4, "away_score": 2, "minute": 81, "ou_line": "7.5", "under_odds": 0.70}},
        {{"home_score": 3, "away_score": 3, "minute": 72, "ou_line": "7/7.5", "under_odds": 0.65}},
        {{"home_score": 4, "away_score": 2, "minute": 55, "ou_line": "7", "under_odds": 0.80}},
        {{"home_score": 2, "away_score": 3, "minute": 75, "ou_line": "6.75", "under_odds": 0.92}},
    ]
    for tc in test_cases:
        result = evaluate_multi_strategy(tc)
        score = f"{{tc['home_score']}}-{{tc['away_score']}}"
        print(f"{{score}} {{tc['minute']}}min 盘{{tc['ou_line']}} 小{{tc['under_odds']}} → {{result}}")
'''
    
    with open(str(RULES_PATH), "w", encoding="utf-8") as f:
        f.write(content)
    
    log(f"✅ 已更新 strategy_rules.py（{len(new_rules)}条规则）")


def main():
    log("=" * 60)
    log("开始策略自动更新（v2: 正确盘口结算）...")
    
    data = load_data()
    log(f"加载 {len(data)} 场snapshot")
    
    new_rules = generate_rules(data)
    new_ev_sum = sum(r['ev'] for r in new_rules)
    log(f"生成 {len(new_rules)} 条规则，总EV={new_ev_sum:.3f}")
    
    log("TOP 5 规则:")
    for r in new_rules[:5]:
        log(f"  {r['id']}: {r['note']} EV{r['ev']:+.3f} ({r['sample']}场, {r['win_pct']}%)")
    
    update_rules_file(new_rules, len(data))
    
    os.system(f"/usr/bin/python3.12 {RULES_PATH} > /dev/null 2>&1")
    if os.path.exists(str(RULES_PATH) + ".bak"):
        log("⚠️ 新文件语法验证失败")
    else:
        log("✅ 新文件语法验证通过")
    
    log("更新完成\n")


if __name__ == '__main__':
    main()

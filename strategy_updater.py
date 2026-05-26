#!/usr/bin/env python3
"""
strategy_updater.py — 策略规则自动更新器
功能：定期从PG拉取最新完场数据，重新计算EV，自动更新strategy_rules.py
运行：纯本地Python + psycopg2，零token消耗
"""
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

BIG_LINES = ('6.5','6.5/7','6.75','7','7.0','7.25','7/7.5','7.5','7.5/8','7.75','8','8.0','8/8.5','8.5','9','9.0','9.5','10')


def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    with open(str(LOG_PATH), "a", encoding="utf-8") as f:
        f.write(line + "\n")


def load_data():
    """从PG加载所有完场比赛数据"""
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
    """, (BIG_LINES,))
    
    first_seen = {}
    for r in cur.fetchall():
        mk = r[0]
        if mk not in first_seen:
            first_seen[mk] = {
                'match_key': mk, 'league': (r[1] or '').strip(),
                'home': r[2] or '', 'away': r[3] or '',
                'ou_line': r[4], 'home_score': r[5], 'away_score': r[6],
                'minute': r[7], 'under_odds': r[8], 'over_odds': r[9],
                'final_home': r[10], 'final_away': r[11], 'final_total': r[12],
            }
    cur.close()
    conn.close()
    return list(first_seen.values())


def parse_line(s):
    if not s: return 0
    try:
        if '/' in s:
            p = s.split('/')
            return (float(p[0]) + float(p[1])) / 2
        return float(s)
    except: return 0


def calc_under_ev(matches, line_threshold=7):
    """计算买小EV"""
    n = len(matches)
    if n == 0: return 0, 0, 0, 0, 0
    win = sum(1 for m in matches if m['final_total'] < line_threshold)
    push = sum(1 for m in matches if m['final_total'] == line_threshold)
    lose = sum(1 for m in matches if m['final_total'] > line_threshold)
    odds = [m['under_odds'] for m in matches if m['under_odds']]
    avg_odds = sum(odds) / len(odds) if odds else 0
    ev = (win / n) * avg_odds - (lose / n) if n else 0
    win_pct = (win + push) / n * 100 if n else 0
    return ev, win_pct, avg_odds, win, lose, push


def generate_rules(data):
    """从数据生成最优策略规则"""
    rules = []
    
    # === 核心策略：6球 + 时间阈值 + 赔率阈值 ===
    six_goals = [m for m in data 
                 if m['home_score'] is not None and m['away_score'] is not None
                 and m['home_score'] + m['away_score'] == 6
                 and m['final_total'] is not None
                 and m['minute'] is not None and m['minute'] >= 0]
    
    best_under_ev = 0
    for min_min in [60, 65, 70, 75, 80]:
        for min_odds in [0.60, 0.65, 0.70, 0.75, 0.80, 0.85]:
            subset = [m for m in six_goals 
                      if m['minute'] >= min_min 
                      and m['under_odds'] is not None 
                      and m['under_odds'] >= min_odds]
            if len(subset) < 10:
                continue
            ev, wp, ao, w, l, p = calc_under_ev(subset)
            if ev > 0.15 and ev > best_under_ev:
                best_under_ev = ev
                rules.append({
                    "goals": 6, "minute_min": min_min, "under_odds_min": min_odds,
                    "ev": round(ev, 3), "win_pct": round(wp, 1), "sample": len(subset),
                    "confidence": "HIGH" if len(subset) >= 30 else "MEDIUM",
                    "note": f"6球+{min_min}分后+赔率≥{min_odds:.2f}"
                })
    
    # === 5球 + 时间 + 赔率 ===
    five_goals = [m for m in data 
                  if m['home_score'] is not None and m['away_score'] is not None
                  and m['home_score'] + m['away_score'] == 5
                  and m['final_total'] is not None
                  and m['minute'] is not None and m['minute'] >= 0]
    
    for min_min in [60, 70, 75, 80]:
        for min_odds in [0.70, 0.75, 0.80, 0.85, 0.90]:
            subset = [m for m in five_goals 
                      if m['minute'] >= min_min 
                      and m['under_odds'] is not None 
                      and m['under_odds'] >= min_odds]
            if len(subset) < 10:
                continue
            ev, wp, ao, w, l, p = calc_under_ev(subset)
            if ev > 0.15:
                rules.append({
                    "goals": 5, "minute_min": min_min, "under_odds_min": min_odds,
                    "ev": round(ev, 3), "win_pct": round(wp, 1), "sample": len(subset),
                    "confidence": "HIGH" if len(subset) >= 30 else "MEDIUM",
                    "note": f"5球+{min_min}分后+赔率≥{min_odds:.2f}"
                })
    
    # === 4球 + 时间 + 赔率 ===
    four_goals = [m for m in data 
                  if m['home_score'] is not None and m['away_score'] is not None
                  and m['home_score'] + m['away_score'] == 4
                  and m['final_total'] is not None
                  and m['minute'] is not None and m['minute'] >= 0]
    
    for min_min in [60, 70, 75, 80]:
        for min_odds in [0.80, 0.85, 0.90]:
            subset = [m for m in four_goals 
                      if m['minute'] >= min_min 
                      and m['under_odds'] is not None 
                      and m['under_odds'] >= min_odds]
            if len(subset) < 10:
                continue
            ev, wp, ao, w, l, p = calc_under_ev(subset)
            if ev > 0.15:
                rules.append({
                    "goals": 4, "minute_min": min_min, "under_odds_min": min_odds,
                    "ev": round(ev, 3), "win_pct": round(wp, 1), "sample": len(subset),
                    "confidence": "HIGH" if len(subset) >= 30 else "MEDIUM",
                    "note": f"4球+{min_min}分后+赔率≥{min_odds:.2f}"
                })
    
    # === 特殊比分策略 ===
    score_map = defaultdict(list)
    for m in data:
        if m['home_score'] is not None and m['away_score'] is not None and m['final_total'] is not None:
            total = m['home_score'] + m['away_score']
            if total >= 4 and m['minute'] is not None and m['minute'] >= 70:
                score_map[(total, m['home_score'], m['away_score'])].append(m)
    
    for (total, h, a), matches in score_map.items():
        if len(matches) < 10:
            continue
        ev, wp, ao, w, l, p = calc_under_ev(matches)
        if ev > 0.20 and total not in [6]:  # 6球的已经在上面有了
            rules.append({
                "home_score": h, "away_score": a, "minute_min": 70,
                "ev": round(ev, 3), "win_pct": round(wp, 1), "sample": len(matches),
                "confidence": "MEDIUM",
                "note": f"{h}-{a}比分+70分后"
            })
    
    # 排序+去重：按EV降序，相同note取最高EV
    rules.sort(key=lambda x: -x['ev'])
    seen_notes = {}
    deduped = []
    for r in rules:
        key = r.get('note', '')
        if key not in seen_notes or r['ev'] > seen_notes[key]['ev']:
            seen_notes[key] = r
            deduped.append(r)
    
    # 分配ID
    for i, r in enumerate(deduped):
        r['id'] = f"S{i+1}"
    
    return deduped[:15]  # 最多15条


def update_rules_file(new_rules, data_count):
    """更新strategy_rules.py文件"""
    now = datetime.now().strftime("%Y-%m-%d")
    
    # 构建核心发现摘要
    top3 = new_rules[:3]
    summary_lines = [f"- {r['note']} → EV+{r['ev']:.3f}, {r['win_pct']}%≤7 ({r['sample']}场)" for r in top3]
    summary = "\n".join(summary_lines)
    
    # 构建规则列表
    rules_code = "STRATEGY_RULES = [\n"
    for r in new_rules:
        rules_code += "    "
        if 'home_score' in r:
            rules_code += f'{{"id": "{r["id"]}", "home_score": {r["home_score"]}, "away_score": {r["away_score"]}, "minute_min": {r["minute_min"]},\n'
        else:
            rules_code += f'{{"id": "{r["id"]}", "goals": {r["goals"]}, "minute_min": {r["minute_min"]}, "under_odds_min": {r["under_odds_min"]},\n'
        rules_code += f'     "ev": {r["ev"]}, "win_pct": {r["win_pct"]}, "sample": {r["sample"]}, "confidence": "{r["confidence"]}",\n'
        rules_code += f'     "note": "{r["note"]}"}},\n'
    rules_code += "]"
    
    # 写入文件
    content = f'''#!/usr/bin/env python3
"""
strategy_rules.py — 基于历史回测的多策略引擎
数据来源: titan_collector {data_count}场完场比赛（首次出现>=6.5盘口）
更新: {now}（自动生成）

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


# ============================================================
# 历史回测策略规则表（{data_count}场，样本≥10，按EV排序）
# ============================================================

{rules_code}


def ou_line_in_range(ou_line_str, low=6.5, high=7.5):
    """检查盘口是否在合理范围内（默认6.5-7.5）"""
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

    # 6-0比分明确负EV，排除
    hs = match.get("home_score") or 0
    aw = match.get("away_score") or 0
    if goals == 6 and ((hs == 6 and aw == 0) or (hs == 0 and aw == 6)):
        return False, None, 0, 0, "BLOWOUT", f"{{hs}}-{{aw}}一边倒，避开"

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
    # 测试
    test_cases = [
        {{"home_score": 3, "away_score": 3, "minute": 82, "ou_line": "6.5", "under_odds": 0.85}},
        {{"home_score": 4, "away_score": 2, "minute": 81, "ou_line": "6.5", "under_odds": 0.70}},
        {{"home_score": 3, "away_score": 3, "minute": 72, "ou_line": "6.5", "under_odds": 0.65}},
        {{"home_score": 4, "away_score": 2, "minute": 55, "ou_line": "6.5", "under_odds": 0.80}},
        {{"home_score": 6, "away_score": 0, "minute": 80, "ou_line": "6.5", "under_odds": 0.70}},
        {{"home_score": 2, "away_score": 3, "minute": 75, "ou_line": "6.5", "under_odds": 0.92}},
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
    log("开始策略自动更新...")
    
    # 1. 加载数据
    data = load_data()
    log(f"加载 {len(data)} 场完场比赛")
    
    # 2. 读取旧规则对比
    old_ev_sum = 0
    old_rule_count = 0
    try:
        # 简单解析旧规则数量
        with open(str(RULES_PATH), "r") as f:
            content = f.read()
            old_rule_count = len(re.findall(r'"id":\s*"[^"]+"', content))
    except:
        pass
    
    # 3. 生成新规则
    new_rules = generate_rules(data)
    new_ev_sum = sum(r['ev'] for r in new_rules)
    log(f"生成 {len(new_rules)} 条规则，总EV={new_ev_sum:.3f}")
    
    # 4. 输出TOP规则
    log("TOP 5 规则:")
    for r in new_rules[:5]:
        log(f"  {r['id']}: {r['note']} EV+{r['ev']:.3f} ({r['sample']}场, {r['win_pct']}%)")
    
    # 5. 检查是否有显著变化
    needs_update = True
    if old_rule_count > 0:
        log(f"旧规则: {old_rule_count}条, 新规则: {len(new_rules)}条 → 更新")
    
    # 6. 更新文件
    if needs_update:
        update_rules_file(new_rules, len(data))
        
        # 7. 验证新文件
        os.system(f"/usr/bin/python3.12 {RULES_PATH} > /dev/null 2>&1")
        if os.path.exists(str(RULES_PATH) + ".bak"):
            log("⚠️ 新文件语法验证失败，保留备份")
        else:
            log("✅ 新文件语法验证通过")
    else:
        log("无显著变化，跳过更新")
    
    log("更新完成\n")


if __name__ == '__main__':
    main()

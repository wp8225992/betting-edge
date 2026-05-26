#!/usr/bin/env python3
"""
双向分析：买小 vs 买大 edge
数据源: PG titan_collector 524场完场比赛（首次出现>=6.5盘口）
"""
import psycopg2
from collections import defaultdict

PG_DSN = "host=localhost dbname=titan_collector user=betting password=betting123"

def line_to_float(s):
    if not s: return 0
    try:
        if '/' in s:
            parts = s.split('/')
            return (float(parts[0]) + float(parts[1])) / 2
        return float(s)
    except: return 0

def analyze():
    conn = psycopg2.connect(PG_DSN)
    cur = conn.cursor()
    
    # 加载每场比赛首次出现>=6.5盘口的快照
    BIG_LINES = ('6.5','6.5/7','6.75','7','7.0','7.25','7/7.5','7.5','7.5/8','7.75','8','8.0','8/8.5','8.5','9','9.0','9.5','10')
    
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
                'final_total': r[12],
            }
    
    data = list(first_seen.values())
    print(f"加载 {len(data)} 场完场比赛\n")
    
    # ═══════════════════════════════════════════════
    # 分析1: 按当时进球 → 买大edge
    # ═══════════════════════════════════════════════
    print("=" * 70)
    print("买大分析: 按首次出现盘口时的当时总进球")
    print("=" * 70)
    print(f"{'当时进球':<10} {'场数':>5} {'>7场':>5} {'>7率':>6} {'均大赔率':>8} {'大EV':>8} {'<=7率':>6} {'小EV':>8}")
    
    goals_map = defaultdict(list)
    for m in data:
        if m['final_total'] is not None and m['home_score'] is not None and m['away_score'] is not None:
            goals_map[m['home_score'] + m['away_score']].append(m)
    
    for tg in sorted(goals_map.keys()):
        matches = goals_map[tg]
        over_count = sum(1 for m in matches if m['final_total'] > 7)
        under_count = sum(1 for m in matches if m['final_total'] < 7)
        push_count = sum(1 for m in matches if m['final_total'] == 7)
        n = len(matches)
        
        over_odds_list = [m['over_odds'] for m in matches if m['over_odds']]
        avg_over_odds = sum(over_odds_list) / len(over_odds_list) if over_odds_list else 0
        over_rate = over_count / n if n else 0
        over_ev = over_rate * avg_over_odds - (1 - over_rate) if n else 0
        
        under_odds_list = [m['under_odds'] for m in matches if m['under_odds']]
        avg_under_odds = sum(under_odds_list) / len(under_odds_list) if under_odds_list else 0
        under_rate = (under_count + push_count) / n if n else 0
        under_ev = (under_count / n) * avg_under_odds - (over_count / n) if n else 0
        
        print(f"{f'当时{tg}球':<10} {n:>5} {over_count:>5} {over_rate:>5.1%} {avg_over_odds:>8.3f} {over_ev:>+8.3f} {under_rate:>5.1%} {under_ev:>+8.3f}")
    
    # ═══════════════════════════════════════════════
    # 分析2: 当时7球 → 买大edge（已经7球了，还会进更多吗？）
    # ═══════════════════════════════════════════════
    print("\n" + "=" * 70)
    print("当时7球的终场分布（买大的关键场景）")
    print("=" * 70)
    
    seven_goals = [m for m in data 
                   if m['home_score'] is not None and m['away_score'] is not None
                   and m['home_score'] + m['away_score'] == 7
                   and m['final_total'] is not None]
    
    final_dist = defaultdict(int)
    for m in seven_goals:
        final_dist[m['final_total']] += 1
    
    print(f"总计: {len(seven_goals)}场")
    for ft in sorted(final_dist.keys()):
        cnt = final_dist[ft]
        print(f"  终场{ft}球: {cnt}场 ({cnt/len(seven_goals)*100:.1f}%)")
    
    if seven_goals:
        over_7 = sum(1 for m in seven_goals if m['final_total'] > 7)
        over_odds = [m['over_odds'] for m in seven_goals if m['over_odds']]
        avg_oo = sum(over_odds)/len(over_odds) if over_odds else 0
        over_ev = (over_7/len(seven_goals)) * avg_oo - ((len(seven_goals)-over_7)/len(seven_goals))
        print(f"\n买大: >7球 {over_7}/{len(seven_goals)} ({over_7/len(seven_goals)*100:.1f}%), 均赔{avg_oo:.3f}, EV{over_ev:+.3f}")
    
    # ═══════════════════════════════════════════════
    # 分析3: 6球中哪些场景买大有edge
    # ═══════════════════════════════════════════════
    print("\n" + "=" * 70)
    print("6球场景 → 买大 vs 买小 对比")
    print("=" * 70)
    
    six_goals = [m for m in data 
                 if m['home_score'] is not None and m['away_score'] is not None
                 and m['home_score'] + m['away_score'] == 6
                 and m['final_total'] is not None]
    
    # 按比分分组
    score_map = defaultdict(list)
    for m in six_goals:
        score_map[(m['home_score'], m['away_score'])].append(m)
    
    print(f"{'比分':<10} {'场数':>5} {'>7':>4} {'>7率':>6} {'大EV':>8} {'<=7':>4} {'<=7率':>6} {'小EV':>8}")
    for (h, a), matches in sorted(score_map.items(), key=lambda x: -sum(1 for m in x[1] if m['final_total'] > 7)):
        n = len(matches)
        over = sum(1 for m in matches if m['final_total'] > 7)
        under = sum(1 for m in matches if m['final_total'] < 7)
        push = sum(1 for m in matches if m['final_total'] == 7)
        
        oo = [m['over_odds'] for m in matches if m['over_odds']]
        avg_oo = sum(oo)/len(oo) if oo else 0
        over_ev = (over/n) * avg_oo - ((n-over)/n) if n else 0
        
        uo = [m['under_odds'] for m in matches if m['under_odds']]
        avg_uo = sum(uo)/len(uo) if uo else 0
        under_ev = (under/n) * avg_uo - ((n-under-push)/n) if n else 0  # push不算输
        
        marker = "← 买大更优" if over_ev > under_ev and over_ev > 0 else ""
        print(f"{f'{h}-{a}':<10} {n:>5} {over:>4} {over/n:>5.1%} {over_ev:>+8.3f} {under:>4} {under/n:>5.1%} {under_ev:>+8.3f} {marker}")
    
    # ═══════════════════════════════════════════════
    # 分析4: 买大组合策略搜索
    # ═══════════════════════════════════════════════
    print("\n" + "=" * 70)
    print("买大组合策略TOP 15（样本≥10场，按大EV排序）")
    print("=" * 70)
    
    over_strategies = []
    
    for min_goals in [3, 4, 5, 6, 7]:
        for max_goals in range(min_goals, min_goals + 1):
            for min_over_odds in [0.70, 0.75, 0.80, 0.85, 0.90]:
                for min_min in [60, 70, 75, 80]:
                    matches = [m for m in data
                               if m['final_total'] is not None
                               and m['home_score'] is not None and m['away_score'] is not None
                               and min_goals <= (m['home_score'] + m['away_score']) <= max_goals
                               and m['over_odds'] is not None and m['over_odds'] >= min_over_odds
                               and m['minute'] is not None and m['minute'] >= min_min]
                    if len(matches) < 10:
                        continue
                    n = len(matches)
                    over = sum(1 for m in matches if m['final_total'] > 7)
                    oo = [m['over_odds'] for m in matches if m['over_odds']]
                    avg_oo = sum(oo)/len(oo) if oo else 0
                    over_rate = over / n
                    over_ev = over_rate * avg_oo - (1 - over_rate)
                    cond = f"当时{min_goals}-{max_goals}球+大赔≥{min_over_odds:.2f}+{min_min}分后"
                    over_strategies.append((cond, n, over, over_rate, avg_oo, over_ev))
    
    # 去重：相同条件取最大样本
    seen = {}
    for cond, n, over, rate, odds, ev in over_strategies:
        if cond not in seen or n > seen[cond][1]:
            seen[cond] = (cond, n, over, rate, odds, ev)
    
    results = sorted(seen.values(), key=lambda x: -x[5])
    for cond, n, over, rate, odds, ev in results[:15]:
        print(f"  {cond:<50s}: {n:3d}场, >7={over:2d}({rate:.1%}), 均赔{odds:.3f}, EV{ev:+.3f}")
    
    if not results:
        print("  （无样本≥10的买大组合）")
    
    # ═══════════════════════════════════════════════
    # 分析5: 终场>7球的特征
    # ═══════════════════════════════════════════════
    print("\n" + "=" * 70)
    print("终场>7球的比赛特征分析")
    print("=" * 70)
    
    over_matches = [m for m in data if m['final_total'] is not None and m['final_total'] > 7]
    under_matches = [m for m in data if m['final_total'] is not None and m['final_total'] <= 7]
    
    print(f"\n终场>7球: {len(over_matches)}场 ({len(over_matches)/len(data)*100:.1f}%)")
    print(f"终场≤7球: {len(under_matches)}场 ({len(under_matches)/len(data)*100:.1f}%)")
    
    # 当时进球分布对比
    print(f"\n{'当时进球':<10} {'总场':>5} {'>7占比':>7}")
    for tg in sorted(goals_map.keys()):
        n = len(goals_map[tg])
        over = sum(1 for m in goals_map[tg] if m['final_total'] > 7)
        print(f"{f'当时{tg}球':<10} {n:>5} {over/n:>6.1%}")
    
    cur.close()
    conn.close()

if __name__ == '__main__':
    analyze()

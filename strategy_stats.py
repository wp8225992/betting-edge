#!/usr/bin/env python3
"""
strategy_stats.py — 策略表现统计
功能：查看每条策略的实盘表现 vs 回测EV
数据源: PG titan_collector signals 表 + auto_bet_history.db
"""
import psycopg2
import sqlite3
from pathlib import Path
from datetime import datetime
from collections import defaultdict

PG_DSN = "host=localhost dbname=titan_collector user=betting password=betting123"
DB_PATH = Path(__file__).parent / "auto_bet_history.db"


def show_pg_stats():
    """查看PG signals表中的策略表现"""
    try:
        conn = psycopg2.connect(PG_DSN)
        cur = conn.cursor()
        
        # 检查signals表是否有strategy_id列
        cur.execute("""
        SELECT column_name FROM information_schema.columns 
        WHERE table_name = 'signals' ORDER BY ordinal_position;
        """)
        cols = [r[0] for r in cur.fetchall()]
        
        if 'strategy_id' in cols:
            cur.execute("""
            SELECT strategy_id, count(*) as total,
                   sum(CASE WHEN result = 'win' THEN 1 ELSE 0 END) as wins,
                   sum(CASE WHEN result = 'loss' THEN 1 ELSE 0 END) as losses,
                   sum(CASE WHEN result = 'push' THEN 1 ELSE 0 END) as pushes,
                   round(avg(CASE WHEN result IN ('win','push') THEN 1.0 ELSE 0 END), 3) as win_rate
            FROM signals
            WHERE strategy_id IS NOT NULL AND result IN ('win','loss','push')
            GROUP BY strategy_id
            ORDER BY total DESC;
            """)
            rows = cur.fetchall()
            if rows:
                print(f"{'策略':<8} {'总场':>5} {'赢':>4} {'输':>4} {'推':>4} {'胜率':>6}")
                for r in rows:
                    print(f"{r[0]:<8} {r[1]:>5} {r[2]:>4} {r[3]:>4} {r[4]:>4} {r[5]:>5.1%}")
            else:
                print("signals表暂无策略统计（还没有完场信号）")
        else:
            print("signals表没有strategy_id列，需要迁移")
        
        cur.close()
        conn.close()
    except Exception as e:
        print(f"PG查询失败: {e}")


def show_local_stats():
    """查看本地SQLite历史记录"""
    if not DB_PATH.exists():
        print(f"本地记录不存在: {DB_PATH}")
        return
    
    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()
    
    # 检查列
    cur.execute("PRAGMA table_info(bet_history)")
    cols = [r[1] for r in cur.fetchall()]
    
    print(f"\n📊 本地投注历史 ({DB_PATH.name})")
    print("=" * 60)
    
    if 'strategy_id' in cols:
        cur.execute("""
        SELECT strategy_id, count(*) as total,
               sum(CASE WHEN result = 'won' THEN 1 ELSE 0 END) as wins,
               sum(CASE WHEN result = 'lost' THEN 1 ELSE 0 END) as losses
        FROM bet_history
        WHERE result IN ('won','lost')
        GROUP BY strategy_id
        ORDER BY total DESC;
        """)
        rows = cur.fetchall()
        if rows:
            print(f"{'策略':<8} {'总场':>5} {'赢':>4} {'输':>4} {'胜率':>6}")
            for r in rows:
                wr = r[2] / (r[2] + r[3]) if (r[2] + r[3]) > 0 else 0
                print(f"{str(r[0]):<8} {r[1]:>5} {r[2]:>4} {r[3]:>4} {wr:>5.1%}")
        else:
            print("暂无已结算投注")
    else:
        # 没有strategy_id列，显示基础统计
        cur.execute("""
        SELECT count(*), 
               sum(CASE WHEN result = 'won' THEN 1 ELSE 0 END),
               sum(CASE WHEN result = 'lost' THEN 1 ELSE 0 END),
               sum(CASE WHEN result = 'placed' THEN 1 ELSE 0 END)
        FROM bet_history;
        """)
        row = cur.fetchone()
        print(f"总投注: {row[0]}, 赢: {row[1]}, 输: {row[2]}, 进行中: {row[3]}")
        
        # 显示最近记录
        cur.execute("""
        SELECT match_key, bet_side, bet_time, odds, amount, result
        FROM bet_history ORDER BY bet_time DESC LIMIT 10;
        """)
        rows = cur.fetchall()
        if rows:
            print(f"\n最近10笔:")
            for r in rows:
                print(f"  {r[2][:10]} {r[0][:15]} {r[1]} @ {r[3]} x{r[4]} → {r[5]}")
    
    conn.close()


def show_backtest_vs_live():
    """对比回测EV与实盘表现"""
    print("\n" + "=" * 60)
    print("📈 回测EV vs 实盘表现对比")
    print("=" * 60)
    
    # 加载当前策略规则
    import sys
    sys.path.insert(0, str(Path(__file__).parent))
    from strategy_rules import STRATEGY_RULES
    
    print(f"\n当前回测规则 ({len(STRATEGY_RULES)}条):")
    print(f"{'ID':<5} {'规则':<35} {'样本':>5} {'胜率':>6} {'回测EV':>7}")
    for r in STRATEGY_RULES:
        print(f"{r['id']:<5} {r['note']:<35} {r['sample']:>5} {r['win_pct']:>5.1f}% {r['ev']:>+7.3f}")
    
    print("\n💡 说明: 当某策略实盘胜率偏离回测胜率>15%时，")
    print("   strategy_updater会在下次运行时自动调整或移除该规则。")


if __name__ == '__main__':
    show_pg_stats()
    show_local_stats()
    show_backtest_vs_live()

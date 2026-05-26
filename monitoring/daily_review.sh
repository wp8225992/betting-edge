#!/bin/bash
# 每日信号复盘 + 7球统计报告
NOTIFY="/home/ubuntu/betting-edge/monitoring/feishu_notify.py"
WORKDIR="/home/ubuntu/betting-edge/auto_bet"

{
    echo "📊 每日信号复盘"
    echo "================"
    echo ""
    
    # 7球统计
    echo "=== 7球卡点统计 ==="
    /usr/bin/python3.12 ~/.hermes/skills/data-science/seven-goal-line-analysis/scripts/seven_ball_report.py 2>/dev/null
    
    echo ""
    echo "=== 今日投注记录 ==="
    # 从 SQLite 读取今天的投注
    python3 -c "
import sqlite3, os
db = os.path.expanduser('$WORKDIR/signals.db')
if os.path.exists(db):
    conn = sqlite3.connect(db)
    c = conn.cursor()
    c.execute("SELECT count(*) FROM bet_history WHERE date(timestamp)=date('now','+8 hours')")
    cnt = c.fetchone()[0]
    print(f'今日投注: {cnt} 笔')
    conn.close()
" 2>/dev/null
    
    echo ""
    echo "=== 账户余额 ==="
    python3 -c "
import sqlite3, os
db = os.path.expanduser('$WORKDIR/signals.db')
if os.path.exists(db):
    conn = sqlite3.connect(db)
    c = conn.cursor()
    c.execute("SELECT balance FROM bet_history ORDER BY timestamp DESC LIMIT 1")
    row = c.fetchone()
    print(f'最新余额: ¥{row[0]:.2f}' if row else '无数据')
    conn.close()
" 2>/dev/null
} > /tmp/daily_review.txt

MSG=$(cat /tmp/daily_review.txt)
if [ -n "$MSG" ]; then
    /usr/bin/python3.12 "$NOTIFY" "$MSG" 2>/dev/null
fi
rm -f /tmp/daily_review.txt

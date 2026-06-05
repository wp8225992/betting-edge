#!/bin/bash
# nowscore采集器健康检查脚本
# 输出状态信息供cron job读取

cd /home/ubuntu/betting-edge/auto_bet

# 1. 进程是否存活
PID=$(pgrep -f nowscore_collector.py)
if [ -z "$PID" ]; then
    echo "CRITICAL: 采集器进程挂了！正在重启..."
    screen -dmS nowscore python3 /home/ubuntu/betting-edge/auto_bet/nowscore_collector.py
    sleep 5
    PID=$(pgrep -f nowscore_collector.py)
    if [ -z "$PID" ]; then
        echo "CRITICAL: 重启失败！"
    else
        echo "已自动重启, PID=$PID"
    fi
    exit 0
fi

# 2. 最近5分钟有没有新数据
PG="PGPASSWORD=betting123 psql -U betting -h localhost -d titan_collector -t -A"
RECENT=$(eval $PG -c "SELECT count(*) FROM snapshots WHERE odds_source IN ('bet365','crown') AND scan_time > (now() - interval '5 minutes')::text" 2>/dev/null)

# 3. WS错误次数（最近30分钟）
ERRORS=$(grep -c "WebSocket error\|ping/pong timed out" nowscore_collector.log 2>/dev/null || echo 0)
RECENT_ERRORS=$(awk -v cutoff="$(date -d '30 minutes ago' '+%Y-%m-%d %H:%M')" '$0 >= cutoff && /WebSocket error|ping\/pong/' nowscore_collector.log 2>/dev/null | wc -l)

# 4. 最新日志时间
LAST_LOG=$(tail -1 nowscore_collector.log 2>/dev/null | grep -oP '^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}')

# 5. 当前滚球数
LIVE=$(grep "Status:" nowscore_collector.log | tail -1 | grep -oP '\d+ live' | grep -oP '\d+')
WITH_ODDS=$(grep "Status:" nowscore_collector.log | tail -1 | grep -oP '\d+ with odds' | grep -oP '\d+')

# 6. 总数据量
TOTAL_SNAP=$(eval $PG -c "SELECT count(*) FROM snapshots WHERE odds_source IN ('bet365','crown')" 2>/dev/null)
TOTAL_MATCHES=$(eval $PG -c "SELECT count(DISTINCT match_id) FROM snapshots WHERE odds_source IN ('bet365','crown')" 2>/dev/null)

# 输出
if [ "$RECENT" -eq 0 ] 2>/dev/null; then
    echo "⚠️ 最近5分钟无新数据（可能无滚球比赛）"
    echo "进程PID: $PID | 滚球: ${LIVE:-0}场 | 有盘口: ${WITH_ODDS:-0}场"
    echo "最后日志: $LAST_LOG"
elif [ "$RECENT_ERRORS" -gt 5 ]; then
    echo "⚠️ 30分钟内WS断连${RECENT_ERRORS}次，但数据正常"
    echo "5分钟新快照: $RECENT | 滚球: ${LIVE:-0}场"
else
    echo "✅ 采集器正常运行"
    echo "5分钟新快照: $RECENT | 累计: ${TOTAL_SNAP}条/${TOTAL_MATCHES}场"
    echo "滚球: ${LIVE:-0}场 | 有盘口: ${WITH_ODDS:-0}场"
    echo "30分钟WS断连: ${RECENT_ERRORS}次"
fi

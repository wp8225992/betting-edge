#!/bin/bash
# 采集器健康检查 + 自动重启 — 纯 PG 判断，不调大模型
NOTIFY="/home/ubuntu/betting-edge/monitoring/feishu_notify.py"
export PGPASSWORD=betting123
PSQL="psql -U betting -h localhost -d titan_collector -t -A"
WORKDIR="/home/ubuntu/betting-edge/auto_bet"
COLLECTOR="$WORKDIR/nowscore_snapshot_collector.py"

PROBLEMS=()
RESTARTED=0

# 1. 进程检查
PID=$(pgrep -f "nowscore_snapshot_collector.py" | head -1)
if [ -z "$PID" ]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] 采集器进程不存在，自动重启..." >> "$WORKDIR/snapshot_collector.log"
    
    # 杀掉所有残留（旧采集器+新采集器）
    pkill -f "nowscore_collector.py" 2>/dev/null
    pkill -f "nowscore_snapshot_collector.py" 2>/dev/null
    sleep 1
    
    # 直接启动新采集器
    cd "$WORKDIR" && /usr/bin/python3.12 "$COLLECTOR" &
    disown
    RESTARTED=1
    MSG="🔄 采集器进程丢失，已自动重启"
    sleep 5
    # 检查重启结果
    NEW_PID=$(pgrep -f "nowscore_snapshot_collector.py" | head -1)
    if [ -n "$NEW_PID" ]; then
        MSG+=" (PID=$NEW_PID)"
    else
        MSG+=" ⚠️ 重启失败，请手动检查"
    fi
    /usr/bin/python3.12 "$NOTIFY" "$MSG" 2>/dev/null
    echo "RESTARTED: $MSG"
    exit 0
fi

# 2. 内存
MEM_KB=$(ps -o rss= -p "$PID" 2>/dev/null | tr -d ' ')
MEM_MB=$((${MEM_KB:-0} / 1024))
if [ "$MEM_MB" -gt 800 ]; then
    PROBLEMS+=("内存${MEM_MB}MB > 800MB")
    # 直接杀掉让系统重启
    kill "$PID"
    sleep 2
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] 内存超标杀死PID=$PID" >> "$WORKDIR/snapshot_collector.log"
    /usr/bin/python3.12 "$NOTIFY" "🔄 采集器内存${MEM_MB}MB超标，已杀掉等待重启" 2>/dev/null
    echo "KILLED: Memory ${MEM_MB}MB"
    exit 0
fi

# 3. 最近5分钟快照数
RECENT_SNAPS=$($PSQL -c "SELECT count(*) FROM snapshots WHERE odds_source = 'bet365' AND scan_time > (now() - interval '5 minutes')::text" 2>/dev/null)
RECENT_SNAPS=$(echo "$RECENT_SNAPS" | tr -d '[:space:]')

# 4. 当前滚球比赛数
LIVE_MATCHES=$($PSQL -c "SELECT count(DISTINCT match_key) FROM snapshots WHERE odds_source = 'bet365' AND scan_time > (now() - interval '5 minutes')::text" 2>/dev/null)
LIVE_MATCHES=$(echo "$LIVE_MATCHES" | tr -d '[:space:]')

# 5. 最新快照时间
LAST_SNAP=$($PSQL -c "SELECT scan_time FROM snapshots WHERE odds_source = 'bet365' ORDER BY scan_time DESC LIMIT 1" 2>/dev/null)
LAST_SNAP=$(echo "$LAST_SNAP" | tr -d '[:space:]')

# 判断：有滚球但无新快照 → 僵死，重启
# 关键：LIVE_MATCHES>0但RECENT_SNAPS=0可能是比赛刚结束，DB残留数据在5分钟窗口内
# 所以只重启当且仅当：内存里确认有live比赛 + DB无新快照
if [ "${RECENT_SNAPS:-0}" -eq 0 ] && [ "${LIVE_MATCHES:-0}" -gt 0 ]; then
    # 保险：检查进程内存里是否真有live比赛（通过WS最近消息时间判断）
    # 如果采集器最近收到过WS数据，说明它在工作，只是比赛结束了
    # 看WS心跳：如果5分钟内WS正常但无快照，说明是真的僵死
    # 简单做法：看最新的快照是否超过3分钟（给比赛结束留缓冲）
    SNAP_AGE=$($PSQL -c "SELECT EXTRACT(EPOCH FROM now() - (SELECT max(scan_time) FROM snapshots WHERE odds_source IN ('bet365','crown')))" 2>/dev/null)
    SNAP_AGE=$(echo "$SNAP_AGE" | tr -d '[:space:]')
    
    if [ -n "$SNAP_AGE" ] && [ "$(echo "$SNAP_AGE > 300" | bc 2>/dev/null)" = "1" ]; then
        # 最新快照超过5分钟，确认真僵死
        PROBLEMS+=("${LIVE_MATCHES}场残存但最新快照${SNAP_AGE}s前 → 僵死重启")
        pkill -f "nowscore_collector.py" 2>/dev/null
        pkill -f "nowscore_snapshot_collector.py" 2>/dev/null
        sleep 2
        cd "$WORKDIR" && /usr/bin/python3.12 "$COLLECTOR" &
        disown
        RESTARTED=1
        MSG="🔄 采集器僵死，已重启"
        sleep 5
        NEW_PID=$(pgrep -f "nowscore_snapshot_collector.py" | head -1)
        [ -n "$NEW_PID" ] && MSG+=" (PID=$NEW_PID)"
        /usr/bin/python3.12 "$NOTIFY" "$MSG" 2>/dev/null
        echo "RESTARTED: $MSG"
        exit 0
    else
        # 快照还在3-5分钟窗口内，比赛可能刚结束，不重启
        echo "OK: 比赛可能刚结束，快照${SNAP_AGE}s前，等待清理"
        exit 0
    fi
fi

# 无滚球比赛时段
if [ "${RECENT_SNAPS:-0}" -eq 0 ] && [ "${LIVE_MATCHES:-0}" -eq 0 ]; then
    echo "OK: PID=$PID MEM=${MEM_MB}MB 无滚球比赛"
    exit 0
fi

# 正常
if [ ${#PROBLEMS[@]} -gt 0 ]; then
    echo "WARN: ${PROBLEMS[*]}"
else
    echo "OK: PID=$PID MEM=${MEM_MB}MB 滚球=${LIVE_MATCHES} 5min快照=${RECENT_SNAPS} 最新=$LAST_SNAP"
fi

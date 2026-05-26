#!/bin/bash
# 大盘口推送监控 - 检查 alerts.log 有新内容就发飞书
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ALERTS="/home/ubuntu/betting-edge/auto_bet/alerts.log"
NOTIFY="$SCRIPT_DIR/feishu_notify.py"

if [ -f "$ALERTS" ] && [ -s "$ALERTS" ]; then
    CONTENT=$(cat "$ALERTS")
    > "$ALERTS"
    /usr/bin/python3.12 "$NOTIFY" "📊 盘口推送:
$CONTENT" 2>/dev/null
fi

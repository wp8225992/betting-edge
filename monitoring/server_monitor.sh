#!/bin/bash
# 服务器资源监控 - 超过阈值发飞书通知
NOTIFY="/home/ubuntu/betting-edge/monitoring/feishu_notify.py"

MEM_USED=$(free | awk '/^Mem:/{printf "%.0f", $3/$2*100}')
DISK_USED=$(df / | awk 'NR==2{gsub(/%/,""); print $5}')
LOAD1=$(awk '{print $1}' /proc/loadavg)

ALERTS=""
if [ "$MEM_USED" -gt 90 ]; then
    ALERTS="${ALERTS}⚠️ 内存: ${MEM_USED}%\n"
fi
if [ "$DISK_USED" -gt 90 ]; then
    ALERTS="${ALERTS}⚠️ 磁盘: ${DISK_USED}%\n"
fi

if [ -n "$ALERTS" ]; then
    /usr/bin/python3.12 "$NOTIFY" "🖥️ 服务器告警:\n${ALERTS}负载: ${LOAD1}" 2>/dev/null
fi

#!/bin/bash
# 启动/重启 nowscore 快照采集器
cd /home/ubuntu/betting-edge/auto_bet

# 杀掉旧进程
pkill -f nowscore_snapshot_collector.py 2>/dev/null
sleep 1

# 用screen启动
screen -dmS nowscore_snapshot python3 nowscore_snapshot_collector.py
echo "Started in screen session 'nowscore_snapshot'"
echo "查看: screen -r nowscore_snapshot"
echo "日志: tail -f nowscore_snapshot_collector.log"
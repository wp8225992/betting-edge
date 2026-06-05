#!/bin/bash
# 启动/重启 nowscore 采集器
cd /home/ubuntu/betting-edge/auto_bet

# 杀掉旧进程
pkill -f nowscore_collector.py 2>/dev/null
sleep 1

# 用screen启动
screen -dmS nowscore python3 nowscore_collector.py
echo "Started in screen session 'nowscore'"
echo "查看: screen -r nowscore"
echo "日志: tail -f nowscore_collector.log"

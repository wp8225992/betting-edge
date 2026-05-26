#!/bin/bash
# 投注指令执行 - 检查 bet_instruction.json 并执行
INSTRUCTION="/home/ubuntu/betting-edge/auto_bet/bet_instruction.json"
[ -f "$INSTRUCTION" ] || exit 0

ACTION=$(python3 -c "import json;print(json.load(open('$INSTRUCTION')).get('action',''))" 2>/dev/null)
[ "$ACTION" = "bet" ] || exit 0

# 指令已存在，main.py 的 check_manual_bet 会在扫描周期内自动处理
# 此脚本仅做日志记录
echo "$(date '+%Y-%m-%d %H:%M:%S') bet_instruction.json found, main.py will handle it" >> /home/ubuntu/betting-edge/auto_bet/betting.log

#!/bin/bash
# 投注指令执行 - 检查bet_instruction.json
INSTRUCTION="/home/ubuntu/betting-edge/auto_bet/bet_instruction.json"
if [ -f "$INSTRUCTION" ]; then
    ACTION=$(python3 -c "import json; d=json.load(open('$INSTRUCTION')); print(d.get('action',''))" 2>/dev/null)
    if [ "$ACTION" = "bet" ]; then
        /usr/bin/python3.12 "$INSTRUCTION" 2>/dev/null
        rm -f "$INSTRUCTION"
    fi
fi

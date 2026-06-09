#!/usr/bin/env python3
"""
采集器状态监控脚本 (crontab运行)
判断标准:
- 进程存在: pgrep找到PID
- 进程活跃: CPU使用率>0
- 日志活跃: 最近5分钟有新日志输出

触发条件: 任一不满足则发送飞书告警
"""

import subprocess
import sys
import os
import json
import requests
from datetime import datetime, timedelta

# 配置
COLLECTOR_SCRIPT_NAME = "nowscore_snapshot_collector.py"
LOG_FILE = "/Users/linlin/PycharmProjects/betting-edge/auto_bet/nowscore_snapshot_collector.log"

# 飞书配置 - 从.env文件读取
def load_feishu_config():
    """从.env文件加载飞书配置"""
    app_id = ""
    app_secret = ""
    try:
        with open("/Users/linlin/.hermes/.env", "r") as f:
            for line in f:
                if line.startswith("FEISHU_APP_ID"):
                    app_id = line.split("=")[1].strip()
                elif line.startswith("FEISHU_APP_SECRET"):
                    app_secret = line.split("=")[1].strip()
    except Exception as e:
        print(f"读取飞书配置失败: {e}")
    return app_id, app_secret

FEISHU_APP_ID, FEISHU_APP_SECRET = load_feishu_config()
FEISHU_CHAT_ID = "oc_facfa4d99bbd966673eccecc586a39ce"


def check_process_exists():
    """检查采集器进程是否存在，返回python进程的PID"""
    result = subprocess.run(
        ["pgrep", "-f", COLLECTOR_SCRIPT_NAME],
        capture_output=True, text=True
    )
    pids = result.stdout.strip().split("\n")
    
    # 找到真正的python进程（不是bash/caffeinate）
    for pid in pids:
        if not pid:
            continue
        # 检查进程命令行
        cmd_result = subprocess.run(
            ["ps", "-p", pid, "-o", "command="],
            capture_output=True, text=True
        )
        cmd = cmd_result.stdout.strip()
        if "Python" in cmd and COLLECTOR_SCRIPT_NAME in cmd:
            return True, pid
    
    return False, ""


def check_process_active(pid):
    """检查进程是否活跃（状态不是S/Z/T）"""
    if not pid:
        return False, ""
    
    # 用ps检查进程状态
    result = subprocess.run(
        ["ps", "-p", pid, "-o", "state="],
        capture_output=True, text=True
    )
    
    if result.returncode != 0:
        return False, ""
    
    state = result.stdout.strip()
    # Z=zombie, T=stopped 才是异常
    # S=sleeping, R=running, I=idle 都是正常的
    is_active = state not in ["Z", "T", ""]
    
    return is_active, state
def check_log_active():
    """检查日志是否有新输出 (最近5分钟)"""
    try:
        # 获取日志文件最后修改时间
        stat = os.stat(LOG_FILE)
        last_modified = datetime.fromtimestamp(stat.st_mtime)
        now = datetime.now()
        
        # 检查最近5分钟是否有更新
        is_active = (now - last_modified) < timedelta(minutes=5)
        return is_active, last_modified.strftime("%Y-%m-%d %H:%M:%S")
    except FileNotFoundError:
        return False, None
    except Exception as e:
        return False, str(e)


def get_feishu_token():
    """获取飞书access token"""
    url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
    try:
        resp = requests.post(url, json={
            "app_id": FEISHU_APP_ID,
            "app_secret": FEISHU_APP_SECRET
        }, timeout=10)
        data = resp.json()
        return data.get("tenant_access_token", "")
    except Exception as e:
        print(f"获取飞书token失败: {e}")
        return ""


def send_feishu_alert(message):
    """发送飞书告警"""
    token = get_feishu_token()
    if not token:
        print("无法发送飞书消息: token获取失败")
        return False
    
    url = "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id"
    headers = {"Authorization": f"Bearer {token}"}
    data = {
        "receive_id": FEISHU_CHAT_ID,
        "msg_type": "text",
        "content": json.dumps({"text": message})
    }
    
    try:
        resp = requests.post(url, headers=headers, json=data, timeout=10)
        return resp.status_code == 200
    except Exception as e:
        print(f"发送飞书消息失败: {e}")
        return False


def main():
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    # 检查1: 进程存在
    exists, pid = check_process_exists()
    
    # 检查2: 进程活跃
    active, state = check_process_active(pid) if exists else (False, "")
    
    # 检查3: 日志活跃
    log_active, log_time = check_log_active()
    
    # 判断状态
    all_ok = exists and active and log_active
    
    if all_ok:
        # 正常状态，只输出日志
        print(f"[{now}] 采集器运行正常 PID={pid} 状态={state} 日志={log_time}")
        return
    
    # 异常状态，发送飞书告警
    issues = []
    if not exists:
        issues.append("进程不存在")
    elif not active:
        issues.append(f"进程异常 状态={state}")
    if not log_active:
        issues.append(f"日志停止更新 (最后更新: {log_time})")
    
    alert_msg = f"""⚠️ 采集器异常告警
时间: {now}
问题: {', '.join(issues)}
PID: {pid if pid else '无'}

请检查采集器状态!"""
    
    print(f"[{now}] 发送告警: {', '.join(issues)}")
    send_feishu_alert(alert_msg)


if __name__ == "__main__":
    main()
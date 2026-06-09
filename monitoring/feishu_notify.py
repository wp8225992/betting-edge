#!/usr/bin/env python3
"""直接调用飞书 API 发消息，不调大模型。
从 /home/ubuntu/.hermes/.env 读取 FEISHU_APP_ID 和 FEISHU_APP_SECRET。
用法: feishu_notify.py <消息文本>
"""
import sys
import json
import os
import urllib.request
import urllib.error

ENV_FILE = "/Users/linlin/.hermes/.env"

def load_env():
    """从 .env 文件读取飞书凭证"""
    creds = {}
    with open(ENV_FILE) as f:
        for line in f:
            line = line.strip()
            if line.startswith("FEISHU_APP_ID="):
                creds["app_id"] = line.split("=", 1)[1].strip()
            elif line.startswith("FEISHU_APP_SECRET="):
                creds["app_secret"] = line.split("=", 1)[1].strip()
    return creds

def get_tenant_token(app_id, app_secret):
    url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
    data = json.dumps({"app_id": app_id, "app_secret": app_secret}).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        result = json.loads(resp.read())
    if result.get("code") != 0:
        raise Exception(f"获取 token 失败: {result}")
    return result["tenant_access_token"]

def send_message(text):
    creds = load_env()
    if not creds.get("app_id") or not creds.get("app_secret"):
        print("错误: 找不到飞书凭证", file=sys.stderr)
        sys.exit(1)

    token = get_tenant_token(creds["app_id"], creds["app_secret"])
    chat_id = "oc_facfa4d99bbd966673eccecc586a39ce"
    url = f"https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id"
    body = {
        "receive_id": chat_id,
        "msg_type": "text",
        "content": json.dumps({"text": text})
    }
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}"
    })
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())
        if result.get("code") != 0:
            print(f"发送失败: {result}", file=sys.stderr)
            sys.exit(1)
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        print(f"HTTP 错误 {e.code}: {body}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: feishu_notify.py <消息文本>", file=sys.stderr)
        sys.exit(1)
    message = "\n".join(sys.argv[1:])
    send_message(message)

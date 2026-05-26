# Betting Edge

足球滚球数据采集与信号分析系统

## 架构

- `core/` - 共享库（数据库、模型、工具）
- `collectors/` - 数据采集器（nowscore WebSocket）
- `strategy/` - 信号检测与分析
- `execution/` - 下注执行
- `tracking/` - 结果追踪与CLV
- `config/` - 配置文件

## 启动

```bash
python -m collectors.nowscore
```

## 配置

复制 config/config.yaml.example 为 config/config.yaml，修改数据库密码等敏感信息。

## 运维

### 重启后恢复

系统重启后 Gateway 和采集器**不会自动启动**，需要手动恢复：

```bash
# 1. 确认 Gateway 已运行（Hermes 会自动恢复）
systemctl --user status hermes-gateway

# 2. 启动采集器
cd ~/betting-edge/auto_bet && /usr/bin/python3.12 nowscore_snapshot_collector.py
```

### 常见问题

**PermissionError: Permission denied on nowscore_snapshot_collector.log**

原因：日志文件被 root 用户创建（可能是之前用 root 运行过）。修复：

```bash
sudo chown -R ubuntu:ubuntu ~/betting-edge/auto_bet/
```

**Another instance running (PID=xxx)**

原因：上次异常退出残留 PID 锁文件。修复：

```bash
rm -f ~/betting-edge/auto_bet/nowscore_snapshot.pid
```

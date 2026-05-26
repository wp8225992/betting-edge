"""统一推送系统 — 替代3份重复的push_alert

支持:
1. 写入alerts.log（现有cron推送到飞书）
2. 直接logging输出
"""

import os
import logging
from pathlib import Path
from .utils import now_bj_str

log = logging.getLogger("alerts")


class AlertManager:
    """统一告警管理"""
    
    def __init__(self, log_file: str = None, feishu_enabled: bool = False):
        self._log_file = log_file
        self._feishu_enabled = feishu_enabled
    
    def push(self, msg: str, level: str = "info"):
        """推送告警
        
        Args:
            msg: 告警消息
            level: 日志级别 (info/warning/error)
        """
        # 1. 日志输出
        getattr(log, level, log.info)(f"ALERT: {msg}")
        
        # 2. 写入alerts.log
        if self._log_file:
            try:
                timestamp = now_bj_str()
                line = f"[{timestamp}] {msg}\n"
                with open(self._log_file, 'a') as f:
                    f.write(line)
            except Exception as e:
                log.error(f"Alert write error: {e}")
    
    def signal(self, emoji: str, title: str, match_info: str, 
               odds_info: str, advice: str = ""):
        """推送信号告警（格式化）"""
        parts = [f"{emoji} {title}", f"  {match_info}", f"  {odds_info}"]
        if advice:
            parts.append(f"  {advice}")
        self.push("\n".join(parts))
    
    def system(self, emoji: str, msg: str):
        """推送系统告警"""
        self.push(f"{emoji} {msg}", level="warning")


# 模块级单例
_manager: AlertManager | None = None


def init_alerts(log_file: str = None, feishu_enabled: bool = False):
    global _manager
    _manager = AlertManager(log_file=log_file, feishu_enabled=feishu_enabled)


def get_alert_manager() -> AlertManager:
    global _manager
    if _manager is None:
        _manager = AlertManager()
    return _manager


def push_alert(msg: str):
    """兼容旧接口"""
    get_alert_manager().push(msg)

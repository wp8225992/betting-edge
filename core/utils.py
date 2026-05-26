"""共享工具 — 日志、时间计算、去重管理"""

import json
import logging
import os
import threading
from datetime import datetime, timezone, timedelta
from pathlib import Path

# 北京时间
BJ = timezone(timedelta(hours=8))


def now_bj() -> datetime:
    """当前北京时间"""
    return datetime.now(BJ)


def now_bj_str() -> str:
    """当前北京时间字符串"""
    return now_bj().strftime("%Y-%m-%d %H:%M:%S")


def today_bj() -> str:
    """今天日期字符串"""
    return now_bj().strftime("%Y-%m-%d")


def setup_logger(name: str, log_dir: str = None, level=logging.INFO) -> logging.Logger:
    """创建带文件+控制台输出的logger"""
    logger = logging.getLogger(name)
    if logger.handlers:  # 避免重复添加
        return logger
    
    logger.setLevel(level)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    
    # 控制台
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    logger.addHandler(sh)
    
    # 文件
    if log_dir:
        os.makedirs(log_dir, exist_ok=True)
        log_path = os.path.join(log_dir, f"{name}.log")
        fh = logging.FileHandler(log_path)
        fh.setFormatter(fmt)
        logger.addHandler(fh)
    
    return logger


def calc_minute(kickoff: str, state: str, current_time: datetime = None) -> int | None:
    """根据开赛时间和当前时间计算比赛分钟数
    
    Args:
        kickoff: 开赛时间 "HH:MM"
        state: 比赛状态码
        current_time: 当前时间(默认北京时间)
    
    Returns:
        比赛分钟数，无法计算返回None
    """
    s = str(state)
    
    # 中场固定45分钟
    if s in ('2', '-11', '半', 'HT', '中'):
        return 45
    
    if not kickoff or ':' not in kickoff:
        return None
    
    try:
        kick_parts = kickoff.split(':')
        kick_h = int(kick_parts[0])
        kick_m = int(kick_parts[1])
        
        if current_time is None:
            current_time = now_bj()
        
        elapsed = (current_time.hour * 60 + current_time.minute) - (kick_h * 60 + kick_m)
        if elapsed < 0:
            elapsed += 24 * 60  # 跨天
        
        # 下半场减去中场休息（约15分钟）
        if s in ('3', '-12', '下半') and elapsed > 60:
            elapsed -= 15
        
        # 合理性检查
        if 0 <= elapsed <= 130:
            return elapsed
    except (ValueError, IndexError):
        pass
    
    return None


class AlertedSet:
    """线程安全的去重集合，支持持久化
    
    替代3份重复的 load_alerted/save_alerted
    """
    
    def __init__(self, filepath: str, max_size: int = 1000):
        self._set: set[str] = set()
        self._lock = threading.Lock()
        self._filepath = filepath
        self._max_size = max_size
        self._load()
    
    def _load(self):
        try:
            with open(self._filepath) as f:
                self._set = set(json.load(f))
        except (FileNotFoundError, json.JSONDecodeError):
            self._set = set()
    
    def _save(self):
        """内部保存，调用时应已持有锁"""
        with open(self._filepath, 'w') as f:
            json.dump(list(self._set), f)
    
    def contains(self, key: str) -> bool:
        with self._lock:
            return key in self._set
    
    def add(self, key: str):
        with self._lock:
            self._set.add(key)
            self._save()
    
    def clear(self):
        with self._lock:
            self._set.clear()
            self._save()
    
    def cleanup_if_needed(self) -> bool:
        """如果超过max_size就清空"""
        with self._lock:
            if len(self._set) > self._max_size:
                old = len(self._set)
                self._set.clear()
                self._save()
                return True
        return False
    
    def __len__(self) -> int:
        with self._lock:
            return len(self._set)
    
    def __contains__(self, key: str) -> bool:
        return self.contains(key)

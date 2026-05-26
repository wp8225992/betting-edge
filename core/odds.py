"""盘口解析工具 — 统一替代3份重复的parse_ou_line"""


def parse_ou_line(ou_str) -> float:
    """解析大小球盘口字符串 → 浮点数
    
    支持格式: '2.5', '2.5/3', '2/2.5', None, ''
    '2.5/3' → (2.5+3)/2 = 2.75 (让半球)
    """
    if not ou_str:
        return 0.0
    try:
        ou_str = str(ou_str).strip()
        if '/' in ou_str:
            parts = ou_str.split('/')
            return (float(parts[0]) + float(parts[1])) / 2
        return float(ou_str)
    except (ValueError, TypeError, IndexError):
        return 0.0


def format_odds(value) -> str:
    """格式化赔率显示"""
    if value is None:
        return '?'
    try:
        return f"{float(value):.2f}"
    except (ValueError, TypeError):
        return str(value)


def expected_line(total_goals: int, minute: int = 80) -> float:
    """根据当前进球数计算正常盘口预期值
    
    80分钟时，正常盘口 ≈ 当前总进球 + 0.5
    """
    return total_goals + 0.5


def is_line_abnormal(ou_val: float, total_goals: int, under_val: float,
                      line_diff_threshold: float = 0.5,
                      under_threshold: float = 1.8) -> tuple[bool, list[str]]:
    """判断盘口是否异常
    
    Returns: (is_abnormal, reasons)
    """
    exp = expected_line(total_goals)
    line_diff = ou_val - exp
    reasons = []
    
    if line_diff >= line_diff_threshold:
        reasons.append(f"盘口偏高{line_diff:.1f}盘(正常{exp})")
    
    if under_val >= under_threshold:
        reasons.append(f"小球水位{under_val:.2f}≥{under_threshold}")
    
    return bool(reasons), reasons

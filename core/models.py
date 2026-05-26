from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime


@dataclass
class Match:
    match_id: str
    league: str = '?'
    home: str = '?'
    away: str = '?'
    kickoff: str = ''
    state: str = '0'
    h_score: int = 0
    a_score: int = 0
    minute: Optional[int] = None
    hdp_open: Optional[str] = None
    ou_open: Optional[str] = None
    last_update: str = ''

    @property
    def total_goals(self) -> int:
        return self.h_score + self.a_score

    @property
    def match_key(self) -> str:
        return f"{self.home}_vs_{self.away}_{self.kickoff}"

    @property
    def is_live(self) -> bool:
        s = str(self.state)
        if s in ('-1', '-99', '完', 'FT', 'AET', 'Pen'):
            return False
        if s in ('1', '2', '3', '-11', '-12', '-13', '-14', '半', 'HT', '中'):
            return True
        if s.lstrip('-').isdigit() and int(s) > 0:
            return True
        if self.minute and isinstance(self.minute, int) and self.minute > 0:
            return True
        return False

    @property
    def is_finished(self) -> bool:
        return str(self.state) in ('-99', '-1', '完', 'FT', 'AET', 'Pen')

    @property
    def is_halftime(self) -> bool:
        return str(self.state) in ('2', '-11', '半', 'HT', '中')

    @property
    def is_second_half(self) -> bool:
        s = str(self.state)
        return s in ('3', '-12', '下半') or (isinstance(self.minute, int) and self.minute > 45)

    @property
    def phase(self) -> int:
        return 2 if self.is_second_half or self.is_halftime else 1

    @property
    def score_str(self) -> str:
        return f"{self.h_score}-{self.a_score}"


@dataclass
class OddsSnapshot:
    source: str  # 'bet365' or 'crown'
    ou_line: Optional[str] = None
    over: Optional[float] = None
    under: Optional[float] = None
    hdp_line: Optional[str] = None
    hdp_home: Optional[float] = None
    hdp_away: Optional[float] = None
    updated: str = ''


@dataclass
class Signal:
    signal_type: str  # 'big_ou_alert' or 'late_abnormal_odds'
    match_id: str
    match: Match
    minute: Optional[int]
    ou_line: str
    ou_open: Optional[str]
    over_odds: Optional[float]
    under_odds: Optional[float]
    odds_source: str
    timestamp: datetime = field(default_factory=datetime.now)
    grade: Optional[str] = None  # '⭐', '📊', None
    advice: str = ''

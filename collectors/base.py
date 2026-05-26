"""采集器基类 — 所有数据源采集器的公共接口"""

import logging
import threading
import time
from abc import ABC, abstractmethod

from core.db import get_pool
from core.alerts import get_alert_manager
from core.utils import AlertedSet


class BaseCollector(ABC):
    """采集器基类
    
    子类实现:
    - start() — 启动采集
    - stop() — 停止采集
    - _run() — 主循环
    """

    def __init__(self, name: str, config: dict):
        self.name = name
        self.config = config
        self.log = logging.getLogger(name)
        self.running = False
        self._threads: list[threading.Thread] = []
        
        # 共享资源
        self.db = get_pool()
        self.alerts = get_alert_manager()
        self.alerted = AlertedSet(
            filepath=config.get("alerted_file", f"alerted_{name}.json"),
            max_size=config.get("alerted_max_size", 1000),
        )

    def start(self):
        """启动采集器"""
        self.running = True
        self.db.init_schema()
        self.log.info(f"{self.name} starting")
        self._run()

    def stop(self):
        """停止采集器"""
        self.running = False
        self.log.info(f"{self.name} stopping")

    @abstractmethod
    def _run(self):
        """主循环 — 子类实现"""
        ...

    def _start_thread(self, target, name: str, daemon: bool = True):
        """启动并注册线程"""
        t = threading.Thread(target=target, daemon=daemon, name=name)
        t.start()
        self._threads.append(t)
        self.log.info(f"Thread '{name}' started")
        return t

    def _check_threads(self):
        """检查线程存活状态，崩溃的自动重启"""
        for i, t in enumerate(self._threads):
            if not t.is_alive():
                self.log.error(f"Thread '{t.name}' died, restarting...")
                self.alerts.system("🔴", f"{t.name}线程崩溃，自动重启")
                new_t = threading.Thread(
                    target=t._target, daemon=t.daemon, name=t.name
                )
                new_t.start()
                self._threads[i] = new_t

    def save_snapshot(self, match, odds_update: dict, source: str):
        """保存盘口快照到PG"""
        from core.utils import now_bj_str
        now = now_bj_str()
        with self.db.get_conn() as conn:
            cur = conn.cursor()
            try:
                cur.execute("""
                    INSERT INTO snapshots 
                    (scan_id, scan_time, match_id, match_key, league, home, away,
                     status_text, phase, home_score, away_score,
                     ou_line, over_odds, under_odds,
                     ou_line_open, over_odds_open, under_odds_open,
                     hdp_line, hdp_home_odds, hdp_away_odds,
                     hdp_line_open, hdp_home_odds_open, hdp_away_odds_open,
                     odds_source, minute)
                    VALUES (0, %s, %s, %s, %s, %s, %s,
                            %s, %s, %s, %s,
                            %s, %s, %s,
                            %s, NULL, NULL,
                            %s, %s, %s,
                            %s, NULL, NULL,
                            %s, %s)
                    ON CONFLICT (match_key, scan_time, odds_source) DO NOTHING
                """, (
                    now, match.match_id, match.match_key, match.league,
                    match.home, match.away,
                    str(match.state), match.phase, match.h_score, match.a_score,
                    odds_update.get("ou_line"), odds_update.get("over"), odds_update.get("under"),
                    str(match.ou_open) if match.ou_open else None,
                    odds_update.get("hdp_line"),
                    odds_update.get("hdp_home"), odds_update.get("hdp_away"),
                    str(match.hdp_open) if match.hdp_open else None,
                    source, match.minute,
                ))
                conn.commit()
            except Exception as e:
                conn.rollback()
                self.log.error(f"Snapshot save error: {e}")
            finally:
                cur.close()

    def save_signal(self, sig_type: str, match, minute, ou_line,
                    ou_open, over, under, source: str):
        """保存信号到PG"""
        from core.utils import now_bj_str
        now = now_bj_str()
        with self.db.get_conn() as conn:
            cur = conn.cursor()
            try:
                cur.execute("""
                    INSERT INTO signals
                    (signal_time, match_id, match_key, league, home, away,
                     signal_type, description, trigger_minute, trigger_score,
                     trigger_ou_line, trigger_ou_line_open, 
                     trigger_over_odds, trigger_under_odds, odds_source)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    now, match.match_id, match.match_key,
                    match.league, match.home, match.away,
                    sig_type, f"{sig_type} from {source}",
                    str(minute) if minute else None, match.score_str,
                    str(ou_line), str(ou_open) if ou_open else None,
                    over, under, source,
                ))
                conn.commit()
            except Exception as e:
                conn.rollback()
                self.log.error(f"Signal save error: {e}")
            finally:
                cur.close()

    def backfill_results(self, match):
        """完场比赛回填results表"""
        from core.utils import now_bj_str, today_bj
        now = now_bj_str()
        today = today_bj()
        with self.db.get_conn() as conn:
            cur = conn.cursor()
            try:
                cur.execute("""
                    INSERT INTO results 
                    (match_id, match_key, match_date, league, home, away,
                     final_home_score, final_away_score, total_goals,
                     first_ou_line, first_over_odds, first_under_odds, first_hdp_line,
                     last_ou_line, last_over_odds, last_under_odds, last_hdp_line,
                     is_finished, updated_at)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,1,%s)
                    ON CONFLICT (match_key) DO UPDATE SET
                        final_home_score=EXCLUDED.final_home_score,
                        final_away_score=EXCLUDED.final_away_score,
                        total_goals=EXCLUDED.total_goals,
                        last_ou_line=EXCLUDED.last_ou_line,
                        last_over_odds=EXCLUDED.last_over_odds,
                        last_under_odds=EXCLUDED.last_under_odds,
                        last_hdp_line=EXCLUDED.last_hdp_line,
                        is_finished=1,
                        updated_at=EXCLUDED.updated_at
                """, (
                    match.match_id, match.match_key, today,
                    match.league, match.home, match.away,
                    match.h_score, match.a_score, match.total_goals,
                    str(match.ou_open) if match.ou_open else None, None, None,
                    str(match.hdp_open) if match.hdp_open else None,
                    None, None, None, None,  # last_* 由调用方补
                    now,
                ))
                conn.commit()
            except Exception as e:
                conn.rollback()
                self.log.debug(f"Result upsert for {match.match_key}: {e}")
            finally:
                cur.close()

    def backfill_signal_results(self, match_id: str, match):
        """回填signals表的最终结果"""
        from core.utils import now_bj_str
        now = now_bj_str()
        with self.db.get_conn() as conn:
            cur = conn.cursor()
            try:
                final_score = match.score_str
                signal_result = f"总{match.total_goals}球"
                cur.execute("""
                    UPDATE signals SET final_score=%s, final_total_goals=%s,
                           signal_result=%s, updated_at=%s
                    WHERE match_id=%s AND final_score IS NULL
                """, (final_score, match.total_goals, signal_result, now, match_id))
                if cur.rowcount > 0:
                    conn.commit()
                    self.log.info(f"Signal backfill: {match_id} → {signal_result}")
                else:
                    conn.rollback()
            except Exception as e:
                conn.rollback()
                self.log.debug(f"Signal backfill for {match_id}: {e}")
            finally:
                cur.close()

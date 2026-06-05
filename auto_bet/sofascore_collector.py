#!/usr/bin/env python3
"""
sofascore_collector.py — SofaScore API 实时比分采集器 (走代理)
数据源: https://api.sofascore.com/api/v1/sport/football/events/live
存储: PostgreSQL titan_collector
代理: 127.0.0.1:7890 (FlClash)
"""
import time
import logging
import requests
import psycopg2
from datetime import datetime, timedelta, timezone

PG_DSN = "host=localhost dbname=titan_collector user=betting password=betting123"
PROXY = {"http": "http://127.0.0.1:7890", "https": "http://127.0.0.1:7890"}

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
                    handlers=[logging.FileHandler("/home/ubuntu/betting-edge/auto_bet/sofascore_collector.log"), logging.StreamHandler()])
log = logging.getLogger("sofascore")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json"
}

def main():
    log.info("Starting SofaScore API Collector via Proxy...")
    conn = psycopg2.connect(PG_DSN)
    cur = conn.cursor()
    
    url = "https://api.sofascore.com/api/v1/sport/football/events/live"
    
    while True:
        try:
            log.info("Fetching live matches from API...")
            resp = requests.get(url, headers=HEADERS, proxies=PROXY, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            
            events = data.get('events', [])
            log.info(f"API returned {len(events)} events")
            
            now = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S")
            saved = 0
            for ev in events:
                # Only process ongoing matches
                if ev.get('status', {}).get('type') == 'finished': continue
                if ev.get('status', {}).get('type') == 'notstarted': continue
                
                home = ev.get('homeTeam', {}).get('name', '')
                away = ev.get('awayTeam', {}).get('name', '')
                h_s = ev.get('homeScore', {}).get('current', 0) or 0
                a_s = ev.get('awayScore', {}).get('current', 0) or 0
                
                # Time
                status = ev.get('status', {}).get('description', '')
                minute = None
                if '1st' in status: minute = ev.get('time', {}).get('currentPeriodStartTimestamp', 0)
                # SofaScore API doesn't give exact minute easily without calculation, 
                # but we can use 'status.description' like "21'"
                # Actually, ev.get('time', {}).get('injuryTime1') exists.
                # Better: use status.description parsing
                import re
                m = re.search(r"(\d+)'", status)
                if m:
                    minute = int(m.group(1))
                elif "HT" in status:
                    minute = 45
                elif "2nd" in status or "2H" in status:
                    # Approximate 45 + current
                    # This is tricky without calculation. 
                    # But we can just store the status text for now.
                    pass
                
                if minute is None: continue # Skip if we can't get accurate minute
                
                match_key = f"{home}_{away}"
                
                try:
                    cur.execute("""
                        INSERT INTO snapshots 
                        (scan_id, scan_time, match_key, league, home, away,
                         status_text, phase, home_score, away_score, minute, 
                         odds_source)
                        VALUES (0, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (match_key, scan_time, odds_source) DO NOTHING
                    """, (now, match_key, ev.get('tournament', {}).get('name', 'Global'), 
                          home, away, status, 2 if minute > 45 else 1, h_s, a_s, minute,
                          'sofascore'))
                    saved += 1
                except Exception as e:
                    log.debug(f"DB Error: {e}")
                    conn.rollback()
            
            conn.commit()
            log.info(f"Saved {saved} records to DB")
            
        except Exception as e:
            log.error(f"Error: {e}")
            conn.rollback()
        
        time.sleep(30)

if __name__ == "__main__":
    main()

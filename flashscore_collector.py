#!/usr/bin/env python3
"""
flashscore_collector.py — FlashScore 实时比分采集器 (走代理)
数据源: https://m.flashscore.com
存储: PostgreSQL titan_collector
代理: 127.0.0.1:7890 (FlClash)
"""
import time
import logging
import psycopg2
from datetime import datetime, timedelta, timezone
from playwright.sync_api import sync_playwright

PG_DSN = "host=localhost dbname=titan_collector user=betting password=betting123"
PROXY = {"server": "http://127.0.0.1:7890"}

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
                    handlers=[logging.FileHandler("/home/ubuntu/betting-edge/auto_bet/flashscore_collector.log"), logging.StreamHandler()])
log = logging.getLogger("flashscore")

def parse_minute(t):
    t = t.strip().replace("'", "")
    if t == "HT": return 45
    if "+" in t:
        parts = t.split("+")
        try: return int(parts[0]) + int(parts[1])
        except: return None
    try: return int(t)
    except: return None

def main():
    log.info("Starting FlashScore Collector via Proxy...")
    conn = psycopg2.connect(PG_DSN)
    cur = conn.cursor()
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, proxy=PROXY, args=['--disable-blink-features=AutomationControlled'])
        context = browser.new_context(
            user_agent='Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1',
            viewport={'width': 390, 'height': 844}
        )
        page = context.new_page()
        
        log.info("Browser launched with proxy")
        
        while True:
            try:
                log.info("Navigating to FlashScore...")
                page.goto("https://m.flashscore.com/", wait_until="domcontentloaded", timeout=30000)
                time.sleep(5)
                # Click LIVE
                try:
                    page.click('text=LIVE', timeout=5000)
                    time.sleep(8)
                except:
                    log.warning("Could not click LIVE button")
                
                matches = page.evaluate("""() => {
                    const rows = document.querySelectorAll('.event__match');
                    const data = [];
                    for (const row of rows) {
                        const timeEl = row.querySelector('.event__time');
                        const statusEl = row.querySelector('.event__stage');
                        let t = timeEl ? timeEl.innerText.trim() : '';
                        let s = statusEl ? statusEl.innerText.trim() : '';
                        
                        // Check if live
                        if (t.includes("'") || t === 'HT' || s.includes("2nd") || s.includes("1st")) {
                            const homeEl = row.querySelector('.event__participant--home');
                            const awayEl = row.querySelector('.event__participant--away');
                            const home = homeEl ? homeEl.innerText.trim() : '';
                            const away = awayEl ? awayEl.innerText.trim() : '';
                            
                            // Score
                            const scoreHome = row.querySelector('.event__score--home');
                            const scoreAway = row.querySelector('.event__score--away');
                            const h_s = scoreHome ? scoreHome.innerText.trim() : '0';
                            const a_s = scoreAway ? scoreAway.innerText.trim() : '0';
                            
                            data.push({
                                time: t,
                                home: home,
                                away: away,
                                score: `${h_s}-${a_s}`
                            });
                        }
                    }
                    return data;
                }""")
                
                log.info(f"Scraped {len(matches)} live matches")
                
                now = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S")
                saved = 0
                for m in matches:
                    minute = parse_minute(m['time'])
                    if minute is None: continue
                    
                    scores = m['score'].split('-')
                    h_s = int(scores[0]) if scores[0].isdigit() else 0
                    a_s = int(scores[1]) if len(scores) > 1 and scores[1].isdigit() else 0
                    
                    match_key = f"{m['home']}_{m['away']}"
                    
                    try:
                        cur.execute("""
                            INSERT INTO snapshots 
                            (scan_id, scan_time, match_key, league, home, away,
                             status_text, phase, home_score, away_score, minute, 
                             odds_source)
                            VALUES (0, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                            ON CONFLICT (match_key, scan_time, odds_source) DO NOTHING
                        """, (now, match_key, "Global", m['home'], m['away'], 
                              str(minute), 2 if minute > 45 else 1, h_s, a_s, minute,
                              'flashscore'))
                        saved += 1
                    except Exception as e:
                        log.debug(f"DB Error: {e}")
                        conn.rollback()
                
                conn.commit()
                log.info(f"Saved {saved} records to DB")
                
            except Exception as e:
                log.error(f"Scrape Error: {e}")
                conn.rollback()
            
            time.sleep(30)

if __name__ == "__main__":
    main()

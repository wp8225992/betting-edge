#!/usr/bin/env python3
"""
flashscore_v2_collector.py — FlashScore Desktop 实时比分采集器 (走代理)
数据源: https://www.flashscore.com
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
                    handlers=[logging.FileHandler("/home/ubuntu/betting-edge/auto_bet/flashscore_v2_collector.log"), logging.StreamHandler()])
log = logging.getLogger("flashscore_v2")

def main():
    log.info("Starting FlashScore Desktop Collector via Proxy...")
    conn = psycopg2.connect(PG_DSN)
    cur = conn.cursor()
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, proxy=PROXY, args=['--disable-blink-features=AutomationControlled'])
        context = browser.new_context(
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
            viewport={'width': 1280, 'height': 800}
        )
        page = context.new_page()
        
        # 拦截不必要的资源加速加载
        page.route("**/*.{png,jpg,jpeg,gif,svg,css,font}", lambda route: route.abort())
        
        log.info("Browser launched with proxy")
        
        while True:
            try:
                log.info("Navigating to FlashScore Live...")
                # Directly go to live soccer page
                page.goto("https://www.flashscore.com/football/?_fsk=0", wait_until="domcontentloaded", timeout=30000)
                time.sleep(5)
                
                # Click "LIVE" tab
                try:
                    page.click('text=LIVE', timeout=5000)
                    time.sleep(10)
                except:
                    log.warning("Could not click LIVE")
                
                # Check if we have matches
                count = page.evaluate("""() => {
                    return document.querySelectorAll('.event__match').length;
                }""")
                log.info(f"Found {count} matches in DOM")
                
                if count > 0:
                    matches = page.evaluate("""() => {
                        const rows = document.querySelectorAll('.event__match');
                        const data = [];
                        for (const row of rows) {
                            // Time is in .event__stage for live matches on desktop
                            const stageEl = row.querySelector('.event__stage');
                            if (!stageEl) continue;
                            const t = stageEl.innerText.trim();
                            // Filter live (contains ' or + or HT)
                            if (!t.includes("'") && t !== 'HT' && !t.includes('+')) continue;
                            
                        const homeEl = row.querySelector('.event__homeParticipant');
                        const awayEl = row.querySelector('.event__awayParticipant');
                        const home = homeEl ? homeEl.innerText.trim() : '';
                        const away = awayEl ? awayEl.innerText.trim() : '';
                            
                            const scoreHome = row.querySelector('.event__score--home');
                            const scoreAway = row.querySelector('.event__score--away');
                            const h_s = scoreHome ? scoreHome.innerText.trim() : '0';
                            const a_s = scoreAway ? scoreAway.innerText.trim() : '0';
                            
                            data.push({time: t, home: home, away: away, score: `${h_s}-${a_s}`});
                        }
                        return data;
                    }""")
                    
                    log.info(f"Scraped {len(matches)} live matches")
                    for m in matches:
                        log.info(f"  {m['home']} vs {m['away']} - {m['time']}")
                    
                    # Save to DB
                    now = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S")
                    saved = 0
                    for m in matches:
                        minute = None
                        t = m['time'].replace("'", "").strip()
                        if t == "HT": minute = 45
                        elif "+" in t:
                            try: minute = sum(int(x) for x in t.split("+") if x.isdigit())
                            except: pass
                        else:
                            try: minute = int(t)
                            except: pass
                        
                        log.debug(f"  Parsing time '{m['time']}' -> minute={minute}")
                        if minute is None: 
                            log.warning(f"  Failed to parse time: {m['time']}")
                            continue
                        
                        scores = m['score'].split('-')
                        h_s = int(scores[0]) if scores[0].isdigit() else 0
                        a_s = int(scores[1]) if len(scores) > 1 and scores[1].isdigit() else 0
                        
                        match_key = f"{m['home']}_{m['away']}"
                        
                        try:
                            match_id = abs(hash(match_key)) % 1000000000
                            cur.execute("""
                                INSERT INTO snapshots 
                                (scan_id, scan_time, match_id, match_key, league, home, away,
                                 status_text, phase, home_score, away_score, minute, 
                                 odds_source)
                                VALUES (0, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                                ON CONFLICT (match_key, scan_time, odds_source) DO NOTHING
                            """, (now, match_id, match_key, "Global", m['home'], m['away'], 
                                  str(minute), 2 if minute > 45 else 1, h_s, a_s, minute,
                                  'flashscore_v2'))
                            saved += 1
                        except Exception as e:
                            log.warning(f"DB Error: {e}")
                            conn.rollback()
                    
                    conn.commit()
                    log.info(f"Saved {saved} records")
                else:
                    log.info("No matches found in DOM yet")
                
            except Exception as e:
                log.error(f"Error: {e}")
                conn.rollback()
            
            time.sleep(30)

if __name__ == "__main__":
    main()

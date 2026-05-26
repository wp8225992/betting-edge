#!/usr/bin/env python3
"""
oddsportal_collector.py — OddsPortal 实时比分采集器 (走代理)
数据源: https://www.oddsportal.com/soccer/livescore/
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
                    handlers=[logging.FileHandler("/home/ubuntu/betting-edge/auto_bet/oddsportal_collector.log"), logging.StreamHandler()])
log = logging.getLogger("oddsportal")

def parse_minute(t):
    t = t.strip().replace("'", "")
    if t == "HT": return 45
    if t.endswith("+"): return int(t[:-1]) + 3
    try: return int(t)
    except: return None

def main():
    log.info("Starting OddsPortal Collector via Proxy...")
    conn = psycopg2.connect(PG_DSN)
    cur = conn.cursor()
    
    # 确保表结构兼容 (复用 snapshots 表)
    try:
        cur.execute("ALTER TABLE snapshots ADD COLUMN IF NOT EXISTS odds_source TEXT DEFAULT 'oddsportal';")
        conn.commit()
    except: pass

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, proxy=PROXY, args=['--disable-blink-features=AutomationControlled'])
        context = browser.new_context(
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
            viewport={'width': 1280, 'height': 800}
        )
        page = context.new_page()
        
        log.info("Browser launched with proxy")
        
        while True:
            try:
                log.info("Navigating to OddsPortal...")
                page.goto("https://www.oddsportal.com/soccer/livescore/", wait_until="domcontentloaded", timeout=30000)
                time.sleep(15) # 等待赔率加载
                
                matches = page.evaluate("""() => {
                    const rows = document.querySelectorAll('tr.active, tr.deactivate, tr.dark, tr.light');
                    const data = [];
                    for (const row of rows) {
                        const timeEl = row.querySelector('.time');
                        if (!timeEl) continue;
                        const t = timeEl.innerText.trim();
                        // 只取滚球 (带 ' 或 HT)
                        if (!t.includes("'") && t !== 'HT' && !t.includes('+')) continue;
                        
                        const teamsEl = row.querySelector('.name');
                        if (!teamsEl) continue;
                        const teams = teamsEl.innerText.split('\\n').map(x => x.trim()).filter(x => x);
                        if (teams.length < 2) continue;
                        
                        const scoreEl = row.querySelector('.score');
                        const score = scoreEl ? scoreEl.innerText.trim() : '0-0';
                        
                        // 尝试获取 OU 赔率 (通常在最后几列)
                        const oddsTds = row.querySelectorAll('td.table-odds');
                        let ou_line = null, ou_under = null;
                        if (oddsTds.length >= 2) {
                            // 倒数第二个通常是 OU
                            const ouTd = oddsTds[oddsTds.length - 2];
                            const spans = ouTd.querySelectorAll('span');
                            if (spans.length >= 3) {
                                ou_line = spans[1].innerText.trim();
                                ou_under = parseFloat(spans[2].innerText);
                            }
                        }
                        
                        data.push({
                            time: t,
                            home: teams[0],
                            away: teams[1],
                            score: score,
                            ou_line: ou_line,
                            ou_under: ou_under
                        });
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
                             odds_source, ou_line, under_odds)
                            VALUES (0, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                            ON CONFLICT (match_key, scan_time, odds_source) DO NOTHING
                        """, (now, match_key, "Global", m['home'], m['away'], 
                              str(minute), 2 if minute > 45 else 1, h_s, a_s, minute,
                              'oddsportal', m['ou_line'], m['ou_under']))
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

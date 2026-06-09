#!/usr/bin/env python3
"""
nowscore_snapshot_collector.py — 捷报比分网浏览器快照采集器
数据源: m.nowscore.com (Playwright浏览器自动化)
存储: PostgreSQL titan_collector

功能:
1. 浏览器自动化打开页面，切换到Bet365盘口
2. 切换到"全部"模式，筛选"开场"比赛
3. 每30秒快照一次，提取完整比赛数据（含分钟数）
3. 信号检测: 大盘≥7 + 80-87分钟异常盘口
5. 飞书推送alerts

优势:
- 简单可靠：无需WS token、连接管理、重连逻辑
- 数据完整：一次快照拿到联赛、开赛时间、分钟数、比分、角球、亚盘、大小球
- 分钟数直接有：页面显示 64'、90+3 等
- 代码量少：约200行 vs 原560行
"""

import json
import time
import re
import os
import sys
import signal
import subprocess
import logging
import requests
from datetime import datetime, timezone, timedelta

import psycopg2
import psycopg2.pool
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

# ─── 配置 ───────────────────────────────────────────────
# 主配置 - 只需要修改这个路径，其他路径自动生成
BASE_DIR = "/Users/linlin/PycharmProjects/betting-edge"
AUTO_BET_DIR = f"{BASE_DIR}/auto_bet"

PG_DSN = "host=localhost dbname=titan_collector user=betting password=betting123 port=5432"
ALERTS_LOG = f"{AUTO_BET_DIR}/alerts.log"
ALERTED_FILE = f"{AUTO_BET_DIR}/alerted.json"

BJ = timezone(timedelta(hours=8))

# ─── 日志 ───────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(f"{AUTO_BET_DIR}/nowscore_snapshot_collector.log"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("nowscore_snapshot")

# 全局状态
matches = {}        # match_id -> {league, home, away, status, minute, h_score, a_score, kickoff, ...}
odds = {}           # match_id -> {bet365: {ou_line, over, under, hdp, ...}}
alerted = set()     # 已推送的信号key
finished_matches = set()  # 已完场并回填过的match_id
bet_placed = {}     # match_id -> {ou_line, under_odds, sig_key, timestamp} 已下注比赛（用于盘口变化检测）
running = True


# ═══════════════════════════════════════════════════════
# 数据库
# ═══════════════════════════════════════════════════════
def init_pool():
    """初始化数据库连接池"""
    global db_pool
    db_pool = psycopg2.pool.ThreadedConnectionPool(1, 5, PG_DSN)
    log.info("DB connection pool initialized (1-5 connections)")


def get_db():
    """从连接池获取连接"""
    return db_pool.getconn()


def put_db(conn):
    """归还连接到池"""
    try:
        db_pool.putconn(conn)
    except Exception:
        pass


def init_db():
    """确保表存在（复用现有schema）"""
    conn = get_db()
    cur = conn.cursor()
    # 用现有的 results / snapshots / signals / scans 表
    # 增加 odds_source 字段区分数据来源
    for tbl, col, typ in [
        ("snapshots", "odds_source", "TEXT DEFAULT 'nowscore_snapshot'"),
        ("snapshots", "minute", "INTEGER"),
        ("signals", "odds_source", "TEXT DEFAULT 'nowscore_snapshot'"),
    ]:
        try:
            cur.execute(f"ALTER TABLE {tbl} ADD COLUMN {col} {typ}")
            conn.commit()
            log.info(f"Added column {tbl}.{col}")
        except psycopg2.errors.DuplicateColumn:
            conn.rollback()
    # 快照去重: 加唯一约束
    try:
        cur.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_snap_dedup
            ON snapshots (match_key, scan_time, odds_source)
        """)
        conn.commit()
        log.info("Added snapshot dedup index")
    except Exception as e:
        conn.rollback()
        log.debug(f"Dedup index: {e}")
    cur.close()
    put_db(conn)


def load_alerted():
    global alerted
    try:
        with open(ALERTED_FILE) as f:
            alerted = set(json.load(f))
        log.info(f"Loaded {len(alerted)} alerted keys")
    except FileNotFoundError:
        alerted = set()


def save_alerted():
    """保存 alerted 到文件"""
    with open(ALERTED_FILE, "w") as f:
        json.dump(list(alerted), f)


# ═══════════════════════════════════════════════════════
# 浏览器快照采集
# ═══════════════════════════════════════════════════════
def parse_ou_line(ou_str):
    """解析大小球盘口字符串为数值，处理复合盘如 '6.5/7' → 6.75"""
    if not ou_str:
        return 0.0
    try:
        s = str(ou_str).strip()
        if '/' in s:
            parts = s.split('/')
            return (float(parts[0]) + float(parts[1])) / 2
        return float(s)
    except (ValueError, IndexError):
        return 0.0


def extract_minute(minute_str):
    """从分钟字符串提取数字，处理 '90+3' → 93, '45' → 45, '完' → None"""
    if not minute_str:
        return None
    minute_str = minute_str.strip()
    if minute_str == '完' or minute_str == 'FT':
        return None  # 完场标记
    if '+' in minute_str:
        parts = minute_str.split('+')
        if len(parts) == 2:
            try:
                base = int(parts[0])
                extra = int(parts[1])
                return base + extra
            except ValueError:
                pass
    else:
        try:
            return int(minute_str)
        except ValueError:
            pass
    return None


def scrape_live_matches(page):
    """使用Playwright页面对象抓取当前进行中的比赛数据"""
    # 执行JavaScript提取所有比赛数据，直接从DOM span元素中获取数值
    js_script = """
    () => {
        const allTables = document.querySelectorAll('table');
        const liveMatches = [];
        
        for (const table of allTables) {
            const stateEl = table.querySelector('.state.red');
            if (!stateEl) continue;
            const minute = stateEl.textContent.trim();
            if (minute === '推迟' || minute === '待定') continue;
            
            const rows = table.querySelectorAll('tr');
            if (rows.length < 5) continue;
            
            // Row 0: league + kickoff time | minute | half score + corners
            const row1tds = rows[0].querySelectorAll('td');
            const leagueTime = row1tds[0] ? row1tds[0].textContent.trim() : '';
            const halfCorner = row1tds[2] ? row1tds[2].textContent.trim() : '';
            
            const timeMatch = leagueTime.match(/(\\d{1,2}:\\d{2})/);
            const kickoffTime = timeMatch ? timeMatch[1] : '';
            const league = leagueTime.replace(/\\d{1,2}:\\d{2}/, '').replace(/补\\d+['′]?/, '').trim();
            
            // Row 1: home | score | away
            const homeEl = rows[1].querySelector('.homeTeam');
            const awayEl = rows[1].querySelector('.guestTeam');
            const homeTeam = homeEl ? homeEl.textContent.trim().replace(/^\\d+/, '') : '';
            const scoreTd = rows[1].querySelectorAll('td')[2];
            const score = scoreTd ? scoreTd.textContent.trim() : '';
            const awayTeam = awayEl ? awayEl.textContent.trim().replace(/^\\d+/, '') : '';
            
            // Row 2: odds (asian handicap | separator | over/under)
            // 直接从span元素中提取数值
            const oddsRow = rows[2];
            let hdp_home = null, hdp_line = null, hdp_away = null;
            let over_odds = null, ou_line = null, under_odds = null;
            
            // 亚洲盘口td
            const asianTd = oddsRow.querySelectorAll('td')[0];
            if (asianTd) {
                const spans = asianTd.querySelectorAll('span');
                if (spans.length >= 3) {
                    // [赔率, 盘口线, 赔率]
                    try { hdp_home = parseFloat(spans[0].textContent); } catch(e) {}
                    hdp_line = spans[1].textContent;
                    try { hdp_away = parseFloat(spans[2].textContent); } catch(e) {}
                } else if (spans.length >= 1) {
                    // 可能只有盘口线
                    hdp_line = spans[0].textContent;
                }
            }
            
            // 大小球td
            const ouTd = oddsRow.querySelectorAll('td')[2];
            if (ouTd) {
                const spans = ouTd.querySelectorAll('span');
                if (spans.length >= 3) {
                    try { over_odds = parseFloat(spans[0].textContent); } catch(e) {}
                    ou_line = spans[1].textContent;
                    try { under_odds = parseFloat(spans[2].textContent); } catch(e) {}
                } else if (spans.length >= 1) {
                    ou_line = spans[0].textContent;
                }
            }
            
            // Row 3: contains match ID in element ID
            const miniBarRow = rows[3];
            let matchId = '';
            if (miniBarRow && miniBarRow.id) {
                const idMatch = miniBarRow.id.match(/trMiniBar_(\\d+)/);
                if (idMatch) {
                    matchId = idMatch[1];
                }
            }
            
            liveMatches.push({
                matchId, league, kickoffTime, minute, homeTeam, score, awayTeam, 
                halfCorner, 
                hdp_home, hdp_line, hdp_away,
                over_odds, ou_line, under_odds
            });
        }
        
        return JSON.stringify(liveMatches);
    }
    """
    
    try:
        result = page.evaluate(js_script)
        matches_data = json.loads(result)
        return matches_data
    except Exception as e:
        log.error(f"JavaScript execution error: {e}")
        return []



def setup_browser_and_page(browser):
    """设置浏览器页面并配置Bet365盘口"""
    page = browser.new_page()
    
    # 访问设置页面切换到Bet365
    try:
        page.goto("https://m.nowscore.com/pageSet.shtml?type=1", timeout=10000)
        # 选择Bet365选项
        page.select_option('select', '36*')
        # 点击确认按钮
        confirm_buttons = page.query_selector_all('text=确认')
        if confirm_buttons:
            confirm_buttons[0].click()
        else:
            # 尝试其他方式
            page.evaluate('() => { if (typeof save === "function") save(); }')
        time.sleep(2)
    except Exception as e:
        log.warning(f"Failed to set Bet365 odds: {e}")
    
    return page


def navigate_to_live_matches(page):
    """导航到进行中比赛页面"""
    try:
        page.goto("https://m.nowscore.com/", timeout=15000)
        time.sleep(3)
        
        # 切换到"全部"模式
        page.evaluate('() => clickScoreType2(0)')
        time.sleep(2)
        
        # 筛选"开场"比赛
        page.evaluate('() => StateFilter(2)')
        time.sleep(2)
        
        return True
    except Exception as e:
        log.error(f"Failed to navigate to live matches: {e}")
        return False


# ═══════════════════════════════════════════════════════
# 数据存储
# ═══════════════════════════════════════════════════════
def is_live_by_minute(minute):
    """根据分钟数判断是否在进行中"""
    if minute is None:
        return False
    return minute >= 0 and minute <= 120  # 包含加时


def save_snapshots(matches_data):
    """保存盘口快照到PG"""
    now = datetime.now(BJ).strftime("%Y-%m-%d %H:%M:%S")
    conn = get_db()
    cur = conn.cursor()
    
    try:
        for match in matches_data:
            match_id = match['matchId']
            if not match_id:
                continue
                
            # 解析比分
            score_parts = match['score'].split('-') if match['score'] else ['0', '0']
            h_score = int(score_parts[0]) if score_parts[0].isdigit() else 0
            a_score = int(score_parts[1]) if len(score_parts) > 1 and score_parts[1].isdigit() else 0
            
            # 解析分钟数
            minute = extract_minute(match['minute'])
            if minute is None:
                continue  # 跳过无法解析分钟的比赛
                
            # 只存滚球比赛
            if not is_live_by_minute(minute):
                continue
                
            # 直接使用从DOM提取的盘口数据
            odds_data = {
                'hdp_home': match.get('hdp_home'),
                'hdp_line': match.get('hdp_line'),
                'hdp_away': match.get('hdp_away'),
                'over': match.get('over_odds'),
                'ou_line': match.get('ou_line'),
                'under': match.get('under_odds'),
            }
            
            match_key = normalize_match_key(match['homeTeam'], match['awayTeam'], match['kickoffTime'])
            total_goals = h_score + a_score
            phase = 2 if minute > 45 else 1
            
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
                        NULL, NULL, NULL,
                        %s, %s, %s,
                        NULL, NULL, NULL,
                        %s, %s)
                ON CONFLICT (match_key, scan_time, odds_source) DO NOTHING
            """, (
                now, match_id, match_key, match['league'],
                match['homeTeam'], match['awayTeam'],
                str(minute), phase,
                h_score, a_score,
                odds_data['ou_line'], odds_data['over'], odds_data['under'],
                odds_data['hdp_line'],
                odds_data['hdp_home'], odds_data['hdp_away'],
                'bet365', minute,
            ))
            
            # 更新全局状态用于信号检测
            matches[match_id] = {
                'match_id': match_id,
                'league': match['league'],
                'home': match['homeTeam'],
                'away': match['awayTeam'],
                'kickoff': match['kickoffTime'],
                'h_score': h_score,
                'a_score': a_score,
                'minute': minute,
                'total_goals': total_goals,
            }
            
            odds[match_id] = {
                'bet365': odds_data
            }
            
        conn.commit()
        saved_count = sum(1 for m in matches_data if extract_minute(m.get('minute', '')) is not None and is_live_by_minute(extract_minute(m.get('minute', ''))))
        log.info(f"Saved {saved_count} live matches to database")
        
    except Exception as e:
        conn.rollback()
        log.error(f"DB save error: {e}")
    finally:
        cur.close()
        put_db(conn)


# ═══════════════════════════════════════════════════════
# 信号检测
# ═══════════════════════════════════════════════════════
def process_signals():
    """检测信号: 大盘≥6.5 + 晚场异常盘口"""
    now = datetime.now(BJ)
    
    for match_id, match in matches.items():
        minute = match.get('minute')
        if minute is None:
            continue  # 只处理有准确分钟数的滚球比赛
        total_goals = match.get('total_goals', 0)
        
        # ── 信号1: 大盘≥6.5 ──
        check_big_ou(match_id, match, total_goals, minute, now)
        
        # ── 信号2: 80-87分钟异常盘口 ──
        if 80 <= minute <= 87:
            check_late_odds(match_id, match, minute, total_goals, now)


def check_big_ou(mid, m, total_goals, minute, now):
    """大盘≥7信号"""
    if minute is None or minute > 87:
        return
    
    match_odds = odds.get(mid, {})
    src_odds = match_odds.get('bet365', {})
    ou_line = src_odds.get('ou_line')
    if not ou_line:
        return
        
    ou_val = parse_ou_line(ou_line)
    if ou_val <= 0 or ou_val < 7:
        return
        
    over = src_odds.get('over', 0)
    under = src_odds.get('under', 0)
    
    # 判断信号等级
    if minute and 75 <= minute <= 87:
        grade = "⭐"
        advice = f"买小胜率78.6%(14场样本) | {minute}分钟高价值窗口"
    elif minute and minute < 45:
        grade = "📊"
        advice = "上半场信号 买小61.5% | 仅供观察"
    elif minute and 45 <= minute <= 74:
        grade = "📊"
        advice = f"中段信号 买小33% | {minute}分钟持续观察"
    else:
        grade = "📊"
        advice = "数据记录"
    
    # 去重key
    if minute and 75 <= minute <= 87:
        window = "late"
    elif minute and minute < 45:
        window = "1h"
    elif minute and 45 <= minute <= 74:
        window = "mid"
    else:
        window = "other"
    sig_key = f"big_ou_{mid}_{window}"
    
    if sig_key in alerted:
        return
        
    alerted.add(sig_key)
    save_alerted()

    # 所有信号都存DB
    save_signal("big_ou_alert", mid, m, minute, ou_line, None, over, under, 'bet365', now)

    # ── 写JSON信号供自动投注决策引擎读取 ──
    sig_json = {
        "sig_key": sig_key,
        "mid": mid,
        "home": m.get('home', ''),
        "away": m.get('away', ''),
        "league": m.get('league', ''),
        "h_score": m.get('h_score', 0),
        "a_score": m.get('a_score', 0),
        "score_str": f"{m.get('h_score', 0)}-{m.get('a_score', 0)}",
        "minute": minute,
        "ou_line": ou_val,
        "ou_line_str": ou_line,
        "over_odds": over,
        "under_odds": under,
        "grade": grade,
        "advice": advice,
        "timestamp": datetime.now(BJ).isoformat(),
    }

    try:
        _append_signal_to_json(sig_json)
    except Exception as e:
        log.error(f"Failed to write signal JSON: {e}")

    # ── 自动投注决策（用已生成的分析数据） ──
    try:
        _auto_bet_decision(sig_json)
    except Exception as e:
        log.error(f"Auto bet decision error: {e}")

    # 只推送有价值的
    if grade:
        msg = (
            f"{grade} 大盘口 [BET365]\n"
            f"  {m.get('league','')} {m.get('home','')} vs {m.get('away','')}\n"
            f"  比分: {m.get('h_score',0)}-{m.get('a_score',0)}  {minute or '?'}分钟\n"
            f"  大小盘: {ou_line}  大{over}/小{under}\n"
            f"  {advice}"
        )
        push_alert(msg)
    else:
        log.info(f"Signal stored (no push): big_ou {m.get('home','?')} vs {m.get('away','?')} {minute}min ou={ou_line}")


def check_late_odds(mid, m, minute, total_goals, now):
    """80-87分钟异常盘口: 盘口比正常高≥0.5 或 小球水位≥1.8"""
    if minute is None or minute > 87:
        return
    
    match_odds = odds.get(mid, {})
    src_odds = match_odds.get('bet365', {})
    ou_line = src_odds.get('ou_line')
    under = src_odds.get('under')
    if not ou_line or not under:
        return
        
    try:
        ou_val = parse_ou_line(ou_line)
        under_val = float(under)
    except (ValueError, TypeError):
        return
        
    if ou_val <= 0:
        return
        
    # 正常80分钟盘口 ≈ 当前总进球 + 0.5
    expected_line = total_goals + 0.5
    line_diff = ou_val - expected_line
    
    is_abnormal = False
    reasons = []
    
    if line_diff >= 0.5:
        is_abnormal = True
        reasons.append(f"盘口偏高{line_diff:.1f}盘(正常{expected_line})")
    
    if under_val >= 1.8:
        is_abnormal = True
        reasons.append(f"小球水位{under_val:.2f}≥1.8")
    
    if not is_abnormal:
        return
        
    # 去重key
    sig_key = f"late_odds_{mid}_bet365"
    if sig_key in alerted:
        return
        
    alerted.add(sig_key)
    save_alerted()

    msg = (
        f"⚠️ 晚场异常盘口 [BET365]\n"
        f"  {m.get('league','')} {m.get('home','')} vs {m.get('away','')}\n"
        f"  比分: {m.get('h_score',0)}-{m.get('a_score',0)}  {minute or '?'}分钟\n"
        f"  大小盘: {ou_line}  大{src_odds.get('over',0)}/小{under}\n"
        f"  {' | '.join(reasons)}\n"
        f"  ⬇️ 80-87分钟大盘买小胜率78.6%"
    )
    push_alert(msg)
    save_signal("late_abnormal_odds", mid, m, minute, ou_line, None,
                 src_odds.get('over'), under, 'bet365', now)


def save_signal(sig_type, mid, m, minute, ou_line, ou_open, over, under, source, now):
    """保存信号到PG"""
    conn = get_db()
    cur = conn.cursor()
    try:
        match_key = normalize_match_key(m.get('home','?'), m.get('away','?'), m.get('kickoff',''))
        score = f"{m.get('h_score',0)}-{m.get('a_score',0)}"
        cur.execute("""
            INSERT INTO signals
            (signal_time, match_id, match_key, league, home, away,
             signal_type, description, trigger_minute, trigger_score,
             trigger_ou_line, trigger_ou_line_open, trigger_over_odds, trigger_under_odds,
             odds_source)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            now.strftime("%Y-%m-%d %H:%M:%S"), mid, match_key,
            m.get("league", ""), m.get("home", ""), m.get("away", ""),
            sig_type, f"{sig_type} from {source}",
            str(minute) if minute else None, score,
            str(ou_line), str(ou_open) if ou_open else None,
            over, under, source,
        ))
        conn.commit()
    except Exception as e:
        conn.rollback()
        log.error(f"Signal save error: {e}")
    finally:
        cur.close()
        put_db(conn)


# ═══════════════════════════════════════════════════════
# 完场结果回填
# ═══════════════════════════════════════════════════════
def check_finished():
    """检查完场比赛，回填results表，清理内存"""
    global finished_matches
    conn = get_db()
    cur = conn.cursor()
    now = datetime.now(BJ).strftime("%Y-%m-%d %H:%M:%S")
    today = datetime.now(BJ).strftime("%Y-%m-%d")
    
    cleaned = 0
    try:
        for mid in list(finished_matches):
            m = matches.get(mid, {})
            if not m:
                finished_matches.discard(mid)
                continue
                
            h = m.get("h_score", 0)
            a = m.get("a_score", 0)
            total = h + a
            match_key = normalize_match_key(m.get('home','?'), m.get('away','?'), m.get('kickoff',''))
            
            # 取最后盘口
            match_odds = odds.get(mid, {})
            last_ou = last_over = last_under = last_hdp = None
            for src in ("bet365", "crown"):
                if src in match_odds and match_odds[src].get("ou_line"):
                    last_ou = match_odds[src]["ou_line"]
                    last_over = match_odds[src].get("over")
                    last_under = match_odds[src].get("under")
                    last_hdp = match_odds[src].get("hdp_line")
                    break
            
            try:
                cur.execute("""
                    INSERT INTO results (match_id, match_key, match_date, league, home, away,
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
                    mid, match_key, today, m.get("league",""), m.get("home",""), m.get("away",""),
                    h, a, total,
                    None, None, None, None,
                    str(last_ou) if last_ou else None, last_over, last_under,
                    str(last_hdp) if last_hdp else None,
                    now,
                ))
            except Exception as e:
                log.debug(f"Result upsert for {match_key}: {e}")
            
            # 回填signals表的最终结果
            final_score = f"{h}-{a}"
            try:
                cur.execute("""
                    UPDATE signals SET final_score=%s, final_total_goals=%s, 
                           signal_result=%s, updated_at=%s
                    WHERE match_id=%s AND final_score IS NULL
                """, (final_score, total, f"总{total}球", now, mid))
                if cur.rowcount > 0:
                    log.info(f"Signal result backfill: {mid} → {final_score}")
            except Exception as e:
                log.debug(f"Signal backfill for {mid}: {e}")
            
            # 清理内存
            del matches[mid]
            odds.pop(mid, None)
            finished_matches.discard(mid)
            cleaned += 1
        
        # 统一commit（避免循环中rollback覆盖前面的commit）
        conn.commit()
        
        # 内存清理：超过120分钟的移除
        stale = [mid for mid, m in matches.items() if m.get('minute', 0) > 120]
        for mid in stale:
            del matches[mid]
            odds.pop(mid, None)
            cleaned += 1
        
        if cleaned > 0:
            log.info(f"Cleaned {cleaned} finished/stale matches (remaining: {len(matches)})")
            
    except Exception as e:
        conn.rollback()
        log.error(f"Check finished error: {e}")
    finally:
        cur.close()
        put_db(conn)


# ═══════════════════════════════════════════════════════
# 推送
# ═══════════════════════════════════════════════════════
def normalize_team_name(name: str) -> str:
    """去掉队名尾部多余数字后缀，保留U20/U19等正常年龄组后缀
    
    例: 洛钦1→洛钦, 卡拉利12→卡拉利, 奥克兰FC1→奥克兰FC, 土库曼U20→土库曼U20(保留)
    """
    name = name.strip()
    # 模式1: 中文字符/括号后跟1-2位数字后缀 → 去掉
    name = re.sub(r'([^A-Za-z0-9])\d{1,2}$', r'\1', name)
    # 模式2: 2+字母组合后跟1-2位数字后缀 → 去掉 (如 FC1, SC2)，不影响 U20 (只有1个字母)
    name = re.sub(r'([A-Za-z]{2,})\d{1,2}$', r'\1', name)
    return name

def normalize_match_key(home: str, away: str, kickoff: str) -> str:
    """生成规范化match_key，消除队名后缀和空kickoff导致的不稳定"""
    h = normalize_team_name(home)
    a = normalize_team_name(away)
    k = kickoff.strip() if kickoff else ''
    return f"{h}_vs_{a}_{k}"

def push_alert(msg):
    """直接推送飞书"""
    now = datetime.now(BJ).strftime("%Y-%m-%d %H:%M:%S")
    log.info(f"ALERT: {msg}")
    
    # 直接推送飞书
    try:
        _send_feishu_alert(msg)
    except Exception as e:
        log.error(f"飞书推送失败: {e}")


def _send_feishu_alert(msg):
    """发送飞书告警"""
    # 从.env读取飞书配置
    feishu_env = "/Users/linlin/.hermes/.env"
    app_id = ""
    app_secret = ""
    
    try:
        with open(feishu_env, "r") as f:
            for line in f:
                if line.startswith("FEISHU_APP_ID"):
                    app_id = line.split("=")[1].strip()
                elif line.startswith("FEISHU_APP_SECRET"):
                    app_secret = line.split("=")[1].strip()
    except Exception as e:
        log.error(f"读取飞书配置失败: {e}")
        return
    
    if not app_id or not app_secret:
        log.error("飞书配置缺失")
        return
    
    # 获取token
    token_url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
    try:
        resp = requests.post(token_url, json={
            "app_id": app_id,
            "app_secret": app_secret
        }, timeout=10)
        data = resp.json()
        token = data.get("tenant_access_token", "")
        if not token:
            log.error(f"获取飞书token失败: {data}")
            return
    except Exception as e:
        log.error(f"获取飞书token失败: {e}")
        return
    
    # 发送消息
    chat_id = "oc_facfa4d99bbd966673eccecc586a39ce"
    send_url = "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id"
    headers = {"Authorization": f"Bearer {token}"}
    
    msg_data = {
        "receive_id": chat_id,
        "msg_type": "text",
        "content": json.dumps({"text": msg})
    }
    
    try:
        resp = requests.post(send_url, headers=headers, json=msg_data, timeout=10)
        if resp.status_code == 200:
            log.info("飞书推送成功")
        else:
            log.error(f"飞书推送失败: {resp.text}")
    except Exception as e:
        log.error(f"飞书推送失败: {e}")


# ═══════════════════════════════════════════════════════
# 自动投注决策（内置，直接用已有分析数据）
# ═══════════════════════════════════════════════════════
LIVE_SIGNALS_FILE = f"{AUTO_BET_DIR}/live_signals.json"
BET_INSTRUCTION_FILE = f"{AUTO_BET_DIR}/bet_instruction.json"
CALIBRATION_FILE = f"{AUTO_BET_DIR}/bet_calibration.json"
BET_HISTORY_FILE = f"{AUTO_BET_DIR}/bet_history.json"
QUERY_RESULT_FILE = "/tmp/bet_query_result.json"

# 回测基准（2026-05-14 7球卡点回测）
BASE_EV = {
    "ou_line": {7.0: 0.091, 7.25: 0.500, 7.5: 0.167, 7.75: 0.077, 8.0: 0.167},
    "score": {"5-1": 0.357, "3-3": 0.308, "2-4": 0.333, "1-5": 0.125, "4-2": -0.083, "6-0": -0.250},
    "time": {(0,30): 0.200, (45,60): 0.333, (60,75): 0.100, (75,87): 0.090},
}


def _get_balance():
    """从查询结果读取最新余额"""
    try:
        with open(QUERY_RESULT_FILE) as f:
            data = json.load(f)
        bal = data.get("balance")
        if bal and bal > 0:
            return bal
    except:
        pass
    # 从bet_calibration读取上次已知余额
    try:
        with open(CALIBRATION_FILE) as f:
            cal = json.load(f)
        return cal.get("last_known_balance", 100)
    except:
        return 100  # 默认值


def _load_calibration():
    try:
        with open(CALIBRATION_FILE) as f:
            return json.load(f)
    except:
        return None


def _save_calibration(cal):
    with open(CALIBRATION_FILE, "w") as f:
        json.dump(cal, f, ensure_ascii=False, indent=2)


def _load_bet_history():
    try:
        with open(BET_HISTORY_FILE) as f:
            return json.load(f)
    except:
        return []


def _save_bet_history(history):
    with open(BET_HISTORY_FILE, "w") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


def _ev_from_backtest(score_str, minute, ou_line, league=""):
    """从回测数据估算EV"""
    # 1. 盘口EV
    rounded = round(ou_line, 2)
    ou_ev = BASE_EV["ou_line"].get(rounded, 0.0)
    
    # 2. 比分EV
    score_ev = BASE_EV["score"].get(score_str, 0.0)
    
    # 3. 时段EV
    time_ev = 0.0
    for (lo, hi), ev in BASE_EV["time"].items():
        if lo <= minute <= hi:
            time_ev = ev
            break
    
    # 加权：盘口40% + 比分40% + 时段20%
    combined = ou_ev * 0.4 + score_ev * 0.4 + time_ev * 0.2
    
    return combined, ou_ev, score_ev, time_ev


def _calc_bet_amount(balance, ev, cal_multiplier, pending_count, max_daily_bets):
    """
    根据余额+EV+校准+待投注数 动态分配金额
    
    策略：
    - 总资金 = balance
    - 保留20%作为缓冲，80%可用于投注
    - 可用资金 = balance * 0.8
    - 每注金额 = 可用资金 * (ev/总EV) * cal_multiplier / 剩余投注次数
    - 最小10元，最大不超过balance*0.3
    """
    # 可用资金（保留20%缓冲）
    usable = balance * 0.8
    
    # 剩余可投注次数
    remaining = max(1, max_daily_bets - pending_count)
    
    # EV权重：EV越高占比越大
    # 归一化：EV范围[0.05, 0.5] → [0.2, 1.0]
    ev_weight = max(0.2, min(1.0, ev / 0.5))
    
    # 基础金额：可用资金 / 剩余次数 * EV权重 * 校准
    base = (usable / remaining) * ev_weight * cal_multiplier
    
    # 边界限制
    amount = max(10, min(int(base), int(balance * 0.3)))
    
    # 确保不超过余额-10（留最低投注余量）
    amount = min(amount, int(balance) - 10)
    
    return max(10, amount)


def _append_signal_to_json(sig):
    """追加信号到live_signals.json"""
    try:
        signals = []
        if os.path.exists(LIVE_SIGNALS_FILE):
            with open(LIVE_SIGNALS_FILE) as f:
                signals = json.load(f)
        signals.append(sig)
        if len(signals) > 100:
            signals = signals[-50:]
        with open(LIVE_SIGNALS_FILE, "w") as f:
            json.dump(signals, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log.error(f"Failed to append live signal: {e}")


def _auto_bet_decision(sig):
    """
    自动投注决策（在信号检测线程中调用，直接用已有分析数据）
    如果决定投注，写bet_instruction.json给执行器
    """
    score_str = sig.get("score_str", "")
    minute = sig.get("minute", 0)
    ou_line = sig.get("ou_line", 0)
    under_odds = sig.get("under_odds", 0)
    league = sig.get("league", "")
    
    # ── 硬过滤 ──
    if ou_line < 7.0 or ou_line > 7.75:
        log.info(f"AUTO_BET SKIP: ou={ou_line} out of range (max 7.75)")
        return
    
    if under_odds < 0.85:
        log.info(f"AUTO_BET SKIP: under_odds={under_odds} too low (min 0.85)")
        return
    
    if minute > 87:
        log.info(f"AUTO_BET SKIP: minute={minute} too late")
        return
    
    # 6-0直接否决（单边碾压庄家定价准）
    if score_str == "6-0":
        log.info(f"AUTO_BET SKIP: 6-0单边碾压，庄家定价准")
        return
    
    # ── 进球速率过滤 ──
    total_goals = sig.get("h_score", 0) + sig.get("a_score", 0)
    if minute > 0:
        goals_per_15min = (total_goals / minute) * 15  # 每15分钟进球数
        
        # 高进球速率比赛必须等到60分钟后再考虑
        if goals_per_15min > 1.5 and minute < 60:
            log.info(f"AUTO_BET SKIP: 进球速率{goals_per_15min:.1f}球/15min，太早不追，等60分钟后再看 ({total_goals}球/{minute}分钟)")
            return
        
        if goals_per_15min > 2.5:
            log.info(f"AUTO_BET SKIP: 进球速率太高 {goals_per_15min:.1f}球/15min ({total_goals}球/{minute}分钟)")
            return
        elif goals_per_15min > 1.5:
            log.info(f"AUTO_BET WARN: 进球速率偏高 {goals_per_15min:.1f}球/15min，降低信心")
    
    # ── EV估算 ──
    ev_combined, ou_ev, score_ev, time_ev = _ev_from_backtest(score_str, minute, ou_line, league)
    
    # ── 加载校准数据（用实战表现覆盖回测基准） ──
    cal = _load_calibration()
    cal_multiplier = 1.0
    
    if cal:
        # 盘口校准
        ou_key = str(rounded) if 'rounded' in dir() else str(round(ou_line, 2))
        ou_cal = cal.get("ou_line_performance", {}).get(ou_key, {})
        if ou_cal.get("bets", 0) >= 5:
            real_ev = ou_cal.get("ev", ev_combined)
            if real_ev < -0.1:
                log.info(f"AUTO_BET SKIP: {ou_key} real EV={real_ev:.3f} (calibrated)")
                return
            cal_multiplier *= max(0.3, ou_cal.get("amount_multiplier", 1.0))
        
        # 比分校准
        score_cal = cal.get("score_performance", {}).get(score_str, {})
        if score_cal.get("bets", 0) >= 3:
            real_ev = score_cal.get("ev", ev_combined)
            if real_ev < -0.1:
                log.info(f"AUTO_BET SKIP: {score_str} real EV={real_ev:.3f} (calibrated)")
                return
            cal_multiplier *= max(0.3, score_cal.get("amount_multiplier", 1.0))
    
    # ── 余额感知动态金额分配 ──
    balance = _get_balance()
    history = _load_bet_history()
    today = datetime.now(BJ).strftime("%Y-%m-%d")
    today_bets = [b for b in history if b.get("date") == today]
    pending_count = len(today_bets)
    daily_pnl = sum(b.get("pnl", 0) for b in today_bets)
    
    if pending_count >= 5:
        log.info("AUTO_BET SKIP: daily limit reached")
        return
    if daily_pnl <= -100:
        log.info(f"AUTO_BET SKIP: daily loss {daily_pnl:.0f}")
        return
    
    # 动态计算金额
    amount = _calc_bet_amount(balance, ev_combined, cal_multiplier, pending_count, max_daily_bets=5)
    
    if ev_combined >= 0.30:
        confidence = "HIGH"
    elif ev_combined >= 0.15:
        confidence = "MEDIUM"
    elif ev_combined >= 0.05:
        confidence = "LOW"
    else:
        log.info(f"AUTO_BET SKIP: EV={ev_combined:.3f} too low")
        return
    
    # 余额不足检查
    if amount > balance - 10:
        log.info(f"AUTO_BET SKIP: amount {amount} > balance-10 ({balance-10})")
        return
    
    # ── 写投注指令 ──
    match_id = sig.get("match_id", "")
    sig_key = sig.get("sig_key", "")
    
    # ⚠️ 第一层：检查是否已下注过这场比赛
    if match_id in bet_placed or sig_key in bet_placed:
        log.info(f"AUTO_BET SKIP: already bet on this match ({match_id})")
        return
    
    # ⚠️ 第二层：检查是否有锁定的文件（执行器正在处理）
    LOCK_FILE = BET_INSTRUCTION_FILE + ".locked"
    if os.path.exists(LOCK_FILE):
        log.info("AUTO_BET SKIP: instruction file locked (executor running)")
        return
    
    # ⚠️ 第三层：检查指令文件是否已存在（防止重复触发）
    if os.path.exists(BET_INSTRUCTION_FILE):
        log.info("AUTO_BET SKIP: instruction file already exists (waiting for executor)")
        return

    instruction = {
        "action": "bet",
        "match": f"{sig['home']} vs {sig['away']}",
        "league": league,
        "side": "under",
        "amount": amount,
        "ou_line": ou_line,
        "under_odds": under_odds,
        "score_str": score_str,
        "minute": minute,
        "ev_estimated": round(ev_combined, 3),
        "ou_ev": round(ou_ev, 3),
        "score_ev": round(score_ev, 3),
        "time_ev": round(time_ev, 3),
        "confidence": confidence,
        "cal_multiplier": round(cal_multiplier, 2),
        "timestamp": sig.get("timestamp", datetime.now(BJ).isoformat()),
        "sig_key": sig.get("sig_key", ""),
    }
    
    with open(BET_INSTRUCTION_FILE, "w") as f:
        json.dump(instruction, f, ensure_ascii=False, indent=2)
    
    # 标记已下注（防止采集器后续轮次重复触发）
    if match_id:
        bet_placed.add(match_id)
    if sig_key:
        bet_placed.add(sig_key)
    
    log.info(f"🎯 AUTO_BET DECISION: {amount}元 小{ou_line} @ {under_odds} | "
             f"{sig['home']} vs {sig['away']} | {score_str} {minute}min | "
             f"EV={ev_combined:.3f} ({confidence}) cal={cal_multiplier:.2f}")
    
    # ── 自动触发执行器（异步，不阻塞采集器） ──
    executor_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "auto_bet_executor.py")
    env = os.environ.copy()
    env['DISPLAY'] = ':99'
    try:
        subprocess.Popen(
            ['/usr/bin/python3.12', executor_path],
            env=env,
            stdout=open(f'{AUTO_BET_DIR}/executor_stdout.log', 'a'),
            stderr=subprocess.STDOUT,
            start_new_session=True,  # 脱离采集器进程组
        )
        log.info(f"🚀 Executor launched (PID auto) for {sig['home']} vs {sig['away']}")
    except Exception as e:
        log.error(f"Failed to launch executor: {e}")


# ═══════════════════════════════════════════════════════
# 主循环
# ═══════════════════════════════════════════════════════
def main():
    global running
    
    # PID文件：防止多实例同时运行
    pid_file = f"{AUTO_BET_DIR}/nowscore_snapshot.pid"
    if os.path.exists(pid_file):
        try:
            with open(pid_file) as f:
                old_pid = int(f.read().strip())
            # 检查进程是否存在且是采集器进程（而不是只检查PID存在）
            result = subprocess.run(["ps", "-p", str(old_pid), "-o", "command="], capture_output=True, text=True)
            cmd = result.stdout.strip()
            if "nowscore_snapshot_collector.py" in cmd:
                log.error(f"Another instance running (PID={old_pid}), exiting")
                return
            else:
                log.info(f"Stale PID file (PID={old_pid}, cmd={cmd}), removing")
                os.remove(pid_file)
        except (ProcessLookupError, ValueError, subprocess.SubprocessError) as e:
            log.info(f"Stale PID file, removing: {e}")
            try:
                os.remove(pid_file)
            except:
                pass
    
    with open(pid_file, "w") as f:
        f.write(str(os.getpid()))
    
    def cleanup_pid():
        try:
            os.remove(pid_file)
        except Exception:
            pass
    
    log.info("=" * 50)
    log.info("nowscore_snapshot_collector starting")
    log.info("Source: m.nowscore.com (Playwright browser automation)")
    log.info("Odds Provider: Bet365")
    log.info("=" * 50)
    
    # 初始化
    init_pool()
    init_db()
    load_alerted()
    
    def handle_signal(sig, frame):
        global running
        log.info(f"Received signal {sig}, shutting down...")
        running = False
        cleanup_pid()
    
    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)
    
    # 浏览器生命周期管理：崩溃自动重启
    browser = None
    page = None
    
    while running:
        try:
            if browser is None:
                log.info("Launching browser...")
                with sync_playwright() as p:
                    browser = p.chromium.launch(headless=True)
                    page = setup_browser_and_page(browser)
                    log.info("Browser launched successfully")
                    
                    while running:
                        try:
                            # 导航到比赛页面
                            if not navigate_to_live_matches(page):
                                log.warning("Failed to navigate, retrying...")
                                time.sleep(10)
                                continue
                            
                            # 抓取比赛数据（含完场）
                            matches_data = scrape_live_matches(page)
                            log.info(f"Scraped {len(matches_data)} matches")
                            
                            if matches_data:
                                live_data = []
                                for m in matches_data:
                                    minute_str = m.get('minute', '')
                                    if minute_str == '完' or minute_str == 'FT':
                                        # 完场比赛：加入finished_matches等待回填
                                        mid = m['matchId']
                                        if mid and mid not in finished_matches:
                                            score_parts = m['score'].split('-') if m['score'] else ['0', '0']
                                            h_score = int(score_parts[0]) if score_parts[0].isdigit() else 0
                                            a_score = int(score_parts[1]) if len(score_parts) > 1 and score_parts[1].isdigit() else 0
                                            matches[mid] = {
                                                'match_id': mid, 'league': m['league'],
                                                'home': m['homeTeam'], 'away': m['awayTeam'],
                                                'kickoff': m['kickoffTime'],
                                                'h_score': h_score, 'a_score': a_score, 'minute': None,
                                            }
                                            finished_matches.add(mid)
                                    else:
                                        live_data.append(m)
                                
                                # 滚球比赛存快照+信号
                                if live_data:
                                    save_snapshots(live_data)
                                    process_signals()
                                
                                # 完场回填+内存清理
                                if finished_matches:
                                    check_finished()
                            
                            time.sleep(30)
                            
                        except PlaywrightTimeout:
                            log.warning("Page timeout, will retry")
                            time.sleep(5)
                            continue
                        except Exception as e:
                            log.error(f"Scrape loop error: {e}", exc_info=True)
                            time.sleep(10)
                            # 浏览器可能已崩溃，跳出内层循环重建
                            break
                    
        except Exception as e:
            log.error(f"Browser crash: {e}")
            now = datetime.now(BJ).strftime("%Y-%m-%d %H:%M:%S")
            try:
                with open(ALERTS_LOG, "a") as f:
                    f.write(f"[{now}] 🔴 浏览器崩溃，自动重启\n")
            except:
                pass
        
        # 清理浏览器资源，准备重启
        try:
            if browser:
                browser.close()
        except:
            pass
        browser = None
        page = None
        
        if running:
            log.info("Restarting browser in 15s...")
            time.sleep(15)
    
    try:
        if browser:
            browser.close()
    except:
        pass
    cleanup_pid()
    
    log.info("nowscore_snapshot_collector stopped")


if __name__ == "__main__":
    main()
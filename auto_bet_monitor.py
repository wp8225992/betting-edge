#!/usr/bin/env python3
"""
FB体育自动投注监控器 — 轻量级常驻版
功能：持续扫描滚球比赛，符合策略条件自动下注10元
策略：7球卡点（当时6球 + 盘口7/7.25/7.5 + 小球赔率≥0.70 → 买小）
"""

import os
import sys
import json
import asyncio
import logging
import sqlite3
import psycopg2
from datetime import datetime, timedelta
from pathlib import Path

from playwright.async_api import async_playwright

# 导入多策略引擎
sys.path.insert(0, str(Path(__file__).parent))
from strategy_rules import evaluate_multi_strategy, parse_ou_line

# ==================== 配置 ====================

SCRIPT_DIR = Path(__file__).parent
CONFIG_PATH = SCRIPT_DIR / "config.json"
DB_PATH = SCRIPT_DIR / "auto_bet_history.db"
LOG_PATH = SCRIPT_DIR / "auto_bet_monitor.log"

# 投注金额（固定10元）
BET_AMOUNT = 10

# 策略配置
STRATEGY = {
    "name": "multi_strategy",
    "description": "基于历史回测的多策略引擎（19条规则，EV>2.0）",
    "ou_line_range": (6.625, 7.625),  # 6.5/7, 7, 7/7.5, 7.25, 7.5
    "under_odds_min": 0.50,  # 放宽由策略规则自身判断
    "min_confidence": "MEDIUM",  # 至少MEDIUM才投
}

# 风控
MAX_DAILY_BETS = 10
MAX_DAILY_LOSS = 200
SCAN_INTERVAL = 30  # 秒

# ==================== 日志 ====================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(str(LOG_PATH), encoding="utf-8"),
        logging.StreamHandler()
    ]
)
log = logging.getLogger("auto_bet")

# ==================== 本地历史记录 ====================

def init_local_db():
    """本地SQLite记录投注历史，防止重复"""
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS bet_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            match_key TEXT NOT NULL,
            bet_side TEXT NOT NULL,
            bet_time TEXT NOT NULL,
            odds REAL,
            amount REAL,
            strategy_id TEXT,
            result TEXT,
            profit REAL
        )
    """)
    # 迁移：旧表可能没有strategy_id列
    try:
        conn.execute("ALTER TABLE bet_history ADD COLUMN strategy_id TEXT")
    except:
        pass  # 列已存在
    conn.execute("""
        CREATE TABLE IF NOT EXISTS daily_stats (
            date TEXT PRIMARY KEY,
            bet_count INTEGER DEFAULT 0,
            total_loss REAL DEFAULT 0
        )
    """)
    conn.commit()
    return conn

def already_bet_today(conn, match_key, bet_side):
    """检查今天是否已对这场比赛下注"""
    today = datetime.now().strftime("%Y-%m-%d")
    cur = conn.execute(
        "SELECT 1 FROM bet_history WHERE match_key=? AND bet_side=? AND bet_time LIKE ?",
        (match_key, bet_side, f"{today}%")
    )
    return cur.fetchone() is not None

def record_bet(conn, match_key, bet_side, odds, amount, strategy_id=None, result="placed"):
    """记录投注"""
    today = datetime.now().strftime("%Y-%m-%d")
    now = datetime.now().isoformat()
    conn.execute(
        "INSERT INTO bet_history (match_key, bet_side, bet_time, odds, amount, strategy_id, result) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (match_key, bet_side, now, odds, amount, strategy_id, result)
    )
    conn.execute(
        "INSERT OR REPLACE INTO daily_stats (date, bet_count, total_loss) VALUES (?, ?, ?)",
        (today, 1, amount)
    )
    conn.execute(
        "UPDATE daily_stats SET bet_count = bet_count + 1 WHERE date = ? AND bet_count = 0",
        (today,)
    )
    conn.commit()
    log.info(f"📝 已记录: {match_key} {bet_side} @ {odds}")

def get_daily_stats(conn):
    """获取今日统计"""
    today = datetime.now().strftime("%Y-%m-%d")
    cur = conn.execute("SELECT bet_count, total_loss FROM daily_stats WHERE date=?", (today,))
    row = cur.fetchone()
    return row if row else (0, 0)

# ==================== PG数据库 ====================

def get_pg_conn():
    """连接PostgreSQL"""
    return psycopg2.connect(
        host="localhost",
        port=5432,
        dbname="titan_collector",
        user="betting",
        password="betting123"
    )

def fetch_live_matches(pg_conn):
    """获取当前滚球比赛（最近10分钟的数据）"""
    query = """
        SELECT DISTINCT ON (match_key)
            match_key, home, away, league,
            home_score, away_score,
            ou_line, over_odds, under_odds,
            minute, status_text,
            scan_time
        FROM snapshots
        WHERE scan_time::timestamp > NOW() - INTERVAL '10 minutes'
          AND (minute > 0 OR status_text LIKE '%%上%%' OR status_text LIKE '%%HT%%')
        ORDER BY match_key, scan_time DESC
    """
    cur = pg_conn.cursor()
    cur.execute(query)
    rows = cur.fetchall()
    
    matches = []
    for row in rows:
        matches.append({
            "match_key": row[0],
            "home": row[1],
            "away": row[2],
            "league": row[3],
            "home_score": row[4],
            "away_score": row[5],
            "ou_line": row[6],
            "over_odds": row[7],
            "under_odds": row[8],
            "minute": row[9],
            "status_text": row[10],
            "created_at": row[11],
        })
    return matches

def parse_ou_line(s):
    """解析盘口"""
    if not s:
        return 0
    try:
        s = str(s).strip()
        if '/' in s:
            parts = s.split('/')
            return (float(parts[0]) + float(parts[1])) / 2
        return float(s)
    except:
        return 0

# ==================== 策略匹配 ====================

def check_strategy(match):
    """
    检查是否匹配策略条件
    核心信号：盘口卡在7/7.25/7.5 这条线 + 小球赔率≥0.70
    返回: (should_bet, reason)
    """
    total_goals = (match["home_score"] or 0) + (match["away_score"] or 0)
    ou_line = parse_ou_line(match["ou_line"])
    under_odds = match["under_odds"]
    
    # 条件1: 盘口在 7~7.5 范围内（核心信号）
    if ou_line < STRATEGY["ou_line_range"][0] or ou_line > STRATEGY["ou_line_range"][1]:
        return False, f"盘{ou_line:.2f}不在范围"
    
    # 条件2: 小球赔率 >= 0.70
    if not under_odds or under_odds < STRATEGY["under_odds_min"]:
        return False, f"赔{under_odds or 0:.3f}<{STRATEGY['under_odds_min']}"
    
    return True, f"⭐ 盘{ou_line:.2f} 球{total_goals} 小{under_odds:.3f}"

# ==================== 浏览器投注 ====================

class AutoBetBrowser:
    """自动投注浏览器"""
    
    def __init__(self):
        self.browser = None
        self.page = None
        self.frame = None
        self.balance = None
    
    async def start(self):
        browser_dir = SCRIPT_DIR / "browser_session_auto"
        browser_dir.mkdir(exist_ok=True)
        
        self.pw = await async_playwright().start()
        self.browser = await self.pw.chromium.launch_persistent_context(
            str(browser_dir),
            headless=False,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
            viewport={"width": 1280, "height": 720},
        )
        self.page = self.browser.pages[0] if self.browser.pages else await self.browser.new_page()
        log.info("浏览器已启动")
    
    async def login(self):
        """登录FB体育"""
        config = json.loads(CONFIG_PATH.read_text())
        site_url = config["site_url"]
        username = config["username"]
        password = config["password"]
        
        # 先检查是否已登录
        try:
            await self.page.goto(site_url, wait_until='domcontentloaded', timeout=15000)
            await asyncio.sleep(1)
            text = await self.page.evaluate('() => document.body?.innerText?.substring(0, 500) || ""')
            if '退出登录' in text or '余额' in text:
                log.info("✅ Session有效，跳过登录")
                self.balance = await self.get_balance()
                log.info(f"💰 体育账户余额: ¥{self.balance}")
                return True
        except:
            pass
        
        # 重新登录
        log.info("🔐 重新登录...")
        await self.page.goto(site_url, wait_until='domcontentloaded', timeout=30000)
        await asyncio.sleep(2)
        
        # 填充账号密码
        await self.page.evaluate('''(p) => {
            const {username, password} = p;
            document.querySelectorAll('input').forEach(inp => {
                const ph = (inp.placeholder || '').toLowerCase();
                const type = inp.type || '';
                if (ph.includes('用户') || ph.includes('username')) {
                    inp.value = username;
                    inp.dispatchEvent(new Event('input', {bubbles: true}));
                }
                if (ph.includes('密码') || ph.includes('pass') || type === 'password') {
                    inp.value = password;
                    inp.dispatchEvent(new Event('input', {bubbles: true}));
                }
            });
        }''', {"username": username, "password": password})
        await asyncio.sleep(0.5)
        
        # 点击登录按钮
        await self.page.evaluate('''() => {
            document.querySelectorAll('input[type="button"]').forEach(btn => {
                if (btn.value?.includes('登录') && btn.offsetParent !== null) btn.click();
            });
        }''')
        await asyncio.sleep(5)
        
        # 关键：登录后必须reload，然后会跳到首页，需要再次goto
        log.info("  刷新页面...")
        try:
            await self.page.reload(wait_until='domcontentloaded', timeout=15000)
        except:
            pass
        await asyncio.sleep(3)
        
        # reload后跳到首页，需要重新goto体育页面
        await self.page.goto(site_url, wait_until='domcontentloaded', timeout=15000)
        await asyncio.sleep(10)  # 等iframe加载
        
        # 关弹窗
        await self.page.evaluate('() => { if(typeof layer !== "undefined") layer.closeAll(); }')
        
        # 验证
        text = await self.page.evaluate('() => document.body?.innerText || ""')
        if '余额' in text or '退出登录' in text:
            log.info("✅ 登录成功")
        else:
            log.warning("⚠️ 登录状态不确定，继续")
        
        self.balance = await self.get_balance()
        log.info(f"💰 体育账户余额: ¥{self.balance}")
        return True
    
    async def get_balance(self):
        """获取体育账户余额"""
        try:
            log.info(f"  当前frames: {[f.url[:60] for f in self.page.frames if f.url]}")
            # 方法1: 体育iframe内查找
            for frame in self.page.frames:
                if "9a877" in (frame.url or "") or "pc." in (frame.url or ""):
                    try:
                        body_text = await frame.inner_text('body')
                        import re
                        log.info(f"  iframe文本长度: {len(body_text)}字")
                        # 直接正则匹配余额
                        m = re.search(r'余额[：:]\s*([0-9,]+\.[0-9]+)', body_text)
                        if m:
                            val = float(m.group(1).replace(',', ''))
                            log.info(f"  匹配到余额行: {m.group(0)}")
                            return val
                        # 备用：找小数格式金额
                        nums = re.findall(r'([0-9]+\.[0-9]{2})', body_text)
                        for n in nums:
                            val = float(n)
                            if 1 < val < 10000:
                                log.info(f"  备用匹配: {val}")
                                return val
                    except Exception as e:
                        log.warning(f"iframe余额读取失败: {e}")

            # 方法2: 主页面扫描
            body_text = await self.page.inner_text('body')
            log.info(f"  主页面文本长度: {len(body_text)}字")
            import re
            m = re.search(r'余额[：:]\s*([0-9,]+\.[0-9]+)', body_text)
            if m:
                return float(m.group(1).replace(',', ''))
            nums = re.findall(r'([0-9]+\.[0-9]{2})', body_text)
            for n in nums:
                val = float(n)
                if 1 < val < 10000:
                    return val
            log.warning("  未找到任何余额数据")
            return None
        except Exception as e:
            log.error(f"获取余额失败: {e}")
            return None
    
    async def get_sports_frame(self):
        """获取体育iframe"""
        for frame in self.page.frames:
            if "9a877" in (frame.url or ""):
                self.frame = frame
                return frame
        
        # 尝试刷新页面
        try:
            await self.page.goto(f"{json.loads(CONFIG_PATH.read_text())['site_url']}/play/89", 
                               timeout=15000, wait_until="domcontentloaded")
            await asyncio.sleep(3)
            for frame in self.page.frames:
                if "9a877" in (frame.url or ""):
                    self.frame = frame
                    return frame
        except:
            pass
        
        log.error("无法获取体育frame")
        return None
    
    async def scan_live_matches(self):
        """扫描FB体育页面所有滚球比赛（含滚动加载），返回结构化列表（含赔率）"""
        frame = await self.get_sports_frame()
        if not frame:
            return []
        
        try:
            # 先滚动到底部加载所有比赛（懒加载页面）
            await frame.evaluate("""
                async () => {
                    const container = document.querySelector('.match-list, .live-match-list, .content-wrapper, .match-container') || document.documentElement;
                    const scrollHeight = container.scrollHeight || document.documentElement.scrollHeight;
                    let lastCount = 0;
                    for (let i = 0; i < 10; i++) {
                        const pos = Math.min((i + 1) * 600, scrollHeight);
                        container.scrollTo({top: pos, behavior: 'auto'});
                        window.scrollTo(0, pos);
                        await new Promise(r => setTimeout(r, 300));
                        const currentCount = document.querySelectorAll('.home-match-info').length;
                        if (currentCount === lastCount) break;
                        lastCount = currentCount;
                    }
                    // 滚回顶部
                    container.scrollTo({top: 0, behavior: 'auto'});
                    window.scrollTo(0, 0);
                }
            """)
            await asyncio.sleep(1)
            
            matches = await frame.evaluate("""
                () => {
                    const items = document.querySelectorAll('.home-match-info');
                    const result = [];
                    for (const item of items) {
                        const teams = item.querySelectorAll('.team-name');
                        if (teams.length < 2) continue;
                        const home = teams[0]?.innerText?.trim() || '';
                        const away = teams[1]?.innerText?.trim() || '';
                        
                        // 比分
                        const scoreEl = item.querySelector('.score, .vs-score, .match-score, .score-live');
                        let hScore = 0, aScore = 0;
                        if (scoreEl) {
                            const scoreText = scoreEl.innerText?.trim() || '';
                            const parts = scoreText.split(/[-–—]/);
                            hScore = parseInt(parts[0]?.trim()) || 0;
                            aScore = parts.length > 1 ? (parseInt(parts[1]?.trim()) || 0) : 0;
                        }
                        
                        // 分钟数（状态栏红色文字）
                        const minEl = item.querySelector('.state.red, .minute, .time-state');
                        const minText = minEl?.innerText?.trim() || '';
                        const minMatch = minText.match(/^(\\d+)/);
                        const minute = minMatch ? parseInt(minMatch[1]) : -1;
                        
                        // 大小球赔率
                        const totalBox = item.querySelector('.match-full-odds-total');
                        let ouLine = null, overOdds = null, underOdds = null;
                        if (totalBox) {
                            const spans = totalBox.querySelectorAll('.value');
                            if (spans.length >= 3) {
                                overOdds = parseFloat(spans[0]?.innerText) || null;
                                ouLine = spans[1]?.innerText?.trim() || null;
                                underOdds = parseFloat(spans[2]?.innerText) || null;
                            }
                        }
                        
                        // 联赛
                        const leagueEl = item.closest('.league-content, .match-group')?.querySelector('.league-name, .type-name');
                        const league = leagueEl?.innerText?.trim() || '';
                        
                        result.push({home, away, hScore, aScore, minute, league, ouLine, overOdds, underOdds});
                    }
                    return result;
                }
            """)
            # 按分钟排序（从早到晚），方便策略优先关注关键时间段
            matches.sort(key=lambda x: x.get("minute", 999))
            return matches
        except Exception as e:
            log.error(f"扫描比赛失败: {e}")
            return []
    
    def check_fb_strategy(self, fb_match):
        """
        多策略引擎判断（基于历史回测）
        返回: (should_bet, strategy_id, reason)
        """
        total_goals = fb_match["hScore"] + fb_match["aScore"]
        minute = fb_match.get("minute", -1)
        ou_line = fb_match.get("ouLine")
        under_odds = fb_match.get("underOdds")
        
        match_data = {
            "home_score": fb_match["hScore"],
            "away_score": fb_match["aScore"],
            "minute": minute,
            "ou_line": ou_line,
            "under_odds": under_odds,
        }
        
        should_bet, strategy_id, ev, win_pct, confidence, note = evaluate_multi_strategy(match_data)
        
        if not should_bet:
            return False, None, note
        
        # 过滤置信度
        conf_order = {"HIGH": 3, "MEDIUM": 2, "LOW": 1}
        min_conf = STRATEGY.get("min_confidence", "MEDIUM")
        if conf_order.get(confidence, 0) < conf_order.get(min_conf, 2):
            return False, None, f"{note} (置信度{confidence}低于{min_conf})"
        
        return True, strategy_id, f"⭐ {note} | EV+{ev:.2f} 胜率{win_pct:.0f}% [{confidence}]"
    
    async def place_bet_by_index(self, idx, fb_match, bet_side, amount):
        """按索引点击FB页面上的比赛并下注"""
        frame = await self.get_sports_frame()
        if not frame:
            return False, "no_frame"
        
        home = fb_match.get("home", "")
        away = fb_match.get("away", "")
        
        try:
            # 1. 按索引点击比赛打开盘口面板
            click_result = await frame.evaluate("""
                async (p) => {
                    const {idx} = p;
                    const items = document.querySelectorAll('.home-match-info');
                    if (idx >= items.length) return {ok: false, err: 'index_out_of_range'};
                    const target = items[idx];
                    const teamContainer = target.querySelector('.team-container, .teams-container');
                    if (teamContainer) teamContainer.click();
                    else target.click();
                    await new Promise(r => setTimeout(r, 500));
                    return {ok: true};
                }
            """, {"idx": idx})
            
            if not click_result.get("ok"):
                return False, click_result.get("err", "click_fail")
            
            # 2. 点击赔率
            odds_idx = 1 if bet_side == "under" else 0
            odds_result = await frame.evaluate("""
                async (params) => {
                    const {oddsIdx} = params;
                    const box = document.querySelector('.match-full-odds-total');
                    if (!box) return {ok: false, err: 'no_total_box'};
                    
                    const items = box.querySelectorAll('.team-odds-item, .value-wrap');
                    const target = items[oddsIdx];
                    if (!target) return {ok: false, err: 'no_odds_item'};
                    
                    const oddsEl = target.querySelector('.value') || target;
                    const odds = parseFloat(oddsEl.innerText?.trim());
                    if (!odds || isNaN(odds)) return {ok: false, err: 'parse_odds_fail'};
                    
                    target.dispatchEvent(new MouseEvent('mousedown', {bubbles: true}));
                    target.dispatchEvent(new MouseEvent('mouseup', {bubbles: true}));
                    target.click();
                    
                    return {ok: true, odds};
                }
            """, {"oddsIdx": odds_idx})
            
            if not odds_result.get("ok"):
                return False, odds_result.get("err", "odds_fail")
            
            odds = odds_result["odds"]
            
            # 3. 输入金额+确认
            confirm_result = await frame.evaluate("""
                async (amount) => {
                    // 等待输入框出现
                    let input = null;
                    for (let i = 0; i < 15; i++) {
                        input = document.querySelector('input.input-value');
                        if (input && input.offsetParent !== null) break;
                        await new Promise(r => setTimeout(r, 150));
                    }
                    if (!input) return {ok: false, err: 'no_input'};
                    
                    input.focus();
                    input.select();
                    input.value = amount.toString();
                    input.dispatchEvent(new Event('input', {bubbles: true}));
                    input.dispatchEvent(new Event('change', {bubbles: true}));
                    await new Promise(r => setTimeout(r, 300));
                    
                    // 找确认按钮
                    let btn = null;
                    for (let i = 0; i < 15; i++) {
                        const allBtns = document.querySelectorAll('button, [class*="btn"], [class*="confirm"]');
                        for (const b of allBtns) {
                            if (b.offsetParent === null) continue;
                            const txt = (b.innerText || '').trim();
                            if ((txt.includes('确认') || txt.includes('Accept') || txt.includes('确定')) && 
                                !b.disabled && !b.classList.contains('disabled') &&
                                !txt.includes('教程')) {
                                btn = b;
                                break;
                            }
                        }
                        if (btn) break;
                        await new Promise(r => setTimeout(r, 150));
                    }
                    if (!btn) return {ok: false, err: 'no_confirm_btn'};
                    
                    btn.click();
                    
                    // 等待结果
                    for (let i = 0; i < 30; i++) {
                        const txt = document.body?.innerText || '';
                        if (txt.includes('投注成功') || txt.includes('Success')) 
                            return {ok: true, msg: 'success'};
                        if (txt.includes('投注失败') || txt.includes('无效') || txt.includes('Fail'))
                            return {ok: false, msg: 'failed'};
                        await new Promise(r => setTimeout(r, 100));
                    }
                    return {ok: true, msg: 'clicked'};
                }
            """, amount)
            
            if not confirm_result.get("ok"):
                return False, confirm_result.get("err", "confirm_fail")
            
            msg = confirm_result.get("msg", "unknown")
            if msg == "success":
                return True, f"success @ {odds:.3f}"
            else:
                return True, f"{msg} @ {odds:.3f}"
                
        except Exception as e:
            return False, f"exception: {str(e)}"
    
    async def stop(self):
        if self.browser:
            await self.browser.close()
        if self.pw:
            await self.pw.stop()
        log.info("浏览器已关闭")

# ==================== 主循环 ====================

async def main():
    log.info(f"{'='*50}")
    log.info(f"FB体育自动投注监控器启动")
    log.info(f"策略: 多策略引擎 (19条规则, 历史回测EV>2.0)")
    log.info(f"投注金额: ¥{BET_AMOUNT}")
    log.info(f"扫描间隔: {SCAN_INTERVAL}秒")
    log.info(f"{'='*50}")
    
    local_db = init_local_db()
    browser = AutoBetBrowser()
    
    # 启动浏览器
    await browser.start()
    logged_in = await browser.login()
    if not logged_in:
        log.error("登录失败，退出")
        await browser.stop()
        local_db.close()
        sys.exit(1)
    
    scan_count = 0
    
    try:
        while True:
            scan_count += 1
            now = datetime.now()
            
            # 风控检查
            bet_count, daily_loss = get_daily_stats(local_db)
            if bet_count >= MAX_DAILY_BETS:
                log.info(f"⛔ 今日已达最大投注次数 {MAX_DAILY_BETS}")
                await asyncio.sleep(300)
                continue
            
            if daily_loss >= MAX_DAILY_LOSS:
                log.info(f"⛔ 今日亏损已达上限 ¥{MAX_DAILY_LOSS}")
                await asyncio.sleep(300)
                continue
            
            # 直接扫描FB体育页面（实时数据，包含比分+赔率+分钟）
            fb_matches = await browser.scan_live_matches()
            if not fb_matches:
                log.info(f"🔍 #{scan_count} [{now.strftime('%H:%M:%S')}] 无滚球比赛")
                await asyncio.sleep(SCAN_INTERVAL)
                continue
            
            log.info(f"🔍 #{scan_count} [{now.strftime('%H:%M:%S')}] {len(fb_matches)}场滚球")
            
            # 扫描成功后读一次余额（此时iframe已加载）
            if scan_count == 1:
                browser.balance = await browser.get_balance()
                if browser.balance:
                    log.info(f"💰 体育账户余额(扫描后): ¥{browser.balance}")
            # 调试：输出分钟数分布
            minutes = [m["minute"] for m in fb_matches if m.get("minute", -1) > 0]
            if minutes:
                log.info(f"   分钟范围: {min(minutes)}-{max(minutes)}min, 平均{sum(minutes)//len(minutes)}min")
            
            # 直接在FB数据上判断策略
            candidates = []
            for i, fb in enumerate(fb_matches):
                should_bet, strategy_id, reason = browser.check_fb_strategy(fb)
                if should_bet:
                    candidates.append((i, fb, strategy_id, reason))
                    log.info(f"🎯 [{i}] {fb['home']} vs {fb['away']} | {fb['hScore']}-{fb['aScore']} {fb['minute']}min | {reason}")
            
            if not candidates:
                await asyncio.sleep(SCAN_INTERVAL)
                continue
            
            # 执行投注
            for idx, fb_match, strategy_id, reason in candidates:
                # 用队名+比分+分钟做match_key防止重复
                match_key = f"{fb_match['home']}_{fb_match['away']}_{fb_match['hScore']}-{fb_match['aScore']}_{fb_match['minute']}min"
                
                # 检查是否已投注
                if already_bet_today(local_db, match_key, "under"):
                    log.info(f"⏭️ 已投注过: {fb_match['home']} vs {fb_match['away']}")
                    continue
                
                log.info(f"🚀 准备投注: {fb_match['home']} vs {fb_match['away']} | {reason}")
                
                # 按索引直接投注（不依赖DB匹配）
                success, result = await browser.place_bet_by_index(
                    idx, fb_match, "under", BET_AMOUNT
                )
                
                if success:
                    record_bet(local_db, match_key, "under", fb_match.get("underOdds", 0), BET_AMOUNT, strategy_id, result)
                    log.info(f"✅ 投注完成: {result}")
                else:
                    log.warning(f"❌ 投注失败: {result}")
                    record_bet(local_db, match_key, "under", fb_match.get("underOdds", 0), BET_AMOUNT, strategy_id, f"failed:{result}")
            
            await asyncio.sleep(SCAN_INTERVAL)
    
    except KeyboardInterrupt:
        log.info("\n🛑 手动停止")
    except Exception as e:
        log.error(f"💥 异常: {e}", exc_info=True)
    finally:
        await browser.stop()
        local_db.close()
        log.info("监控器已关闭")

if __name__ == "__main__":
    asyncio.run(main())

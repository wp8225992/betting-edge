#!/usr/bin/env python3
"""
FB体育自动投注系统 v4 — 快速投注版
核心优化：扫描→定位→点击→输入→确认一气呵成，不sleep、不重复遍历
"""

import os
import sys
import json
import asyncio
import logging
import re
from datetime import datetime, timedelta
from pathlib import Path

from playwright.async_api import async_playwright

# ==================== 配置 ====================

SCRIPT_DIR = Path(__file__).parent
CONFIG_PATH = SCRIPT_DIR / "config.json"
DB_PATH = SCRIPT_DIR / "signals.db"
LOG_PATH = SCRIPT_DIR / "betting.log"

def load_config():
    with open(CONFIG_PATH) as f:
        return json.load(f)

CONFIG = load_config()

# ==================== 日志 ====================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(CONFIG.get("log_file", str(LOG_PATH)), encoding="utf-8"),
        logging.StreamHandler()
    ]
)
log = logging.getLogger("betting")

# ==================== 数据库 ====================

import sqlite3

def init_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            league TEXT,
            home TEXT NOT NULL,
            away TEXT NOT NULL,
            bet_side TEXT NOT NULL,
            market TEXT DEFAULT 'total',
            scan_odds REAL,
            click_odds REAL,
            confirm_odds REAL,
            odds_drift_pct REAL,
            bet_amount REAL,
            action TEXT NOT NULL,
            result TEXT,
            home_score INTEGER,
            away_score INTEGER,
            profit REAL,
            notes TEXT,
            simulation INTEGER DEFAULT 1
        )
    """)
    conn.commit()
    return conn

def record_signal(conn, **kwargs):
    cols = ', '.join(kwargs.keys())
    placeholders = ', '.join(['?' for _ in kwargs])
    conn.execute(f"INSERT INTO signals ({cols}) VALUES ({placeholders})", list(kwargs.values()))
    conn.commit()
    log.info(f"📝 信号: {kwargs.get('home')} vs {kwargs.get('away')} → {kwargs.get('action')}")

# ==================== 浏览器 ====================

class FBSportsBrowser:
    """FB体育浏览器 — 有头模式"""

    def __init__(self, config):
        self.config = config
        self.browser = None
        self.page = None
        self.balance = None
        self.logged_in = False
        self._bet_history = set()

    async def start(self):
        """启动有头浏览器"""
        browser_dir = SCRIPT_DIR / 'browser_session_fast'
        browser_dir.mkdir(exist_ok=True)
        
        self.pw = await async_playwright().start()
        self.browser = await self.pw.chromium.launch_persistent_context(
            str(browser_dir),
            headless=False,  # 有头模式
            viewport={'width': 1920, 'height': 1080},
            ignore_https_errors=True,
            args=['--no-sandbox', '--disable-dev-shm-usage'],
        )
        self.page = self.browser.pages[0] if self.browser.pages else await self.browser.new_page()
        log.info("🌐 浏览器已启动 [有头]")

    async def stop(self):
        if self.browser:
            try:
                await self.browser.close()
            except:
                pass
        if self.pw:
            try:
                await self.pw.stop()
            except:
                pass
        log.info("🌐 浏览器已关闭")

    async def login(self):
        """登录FB体育"""
        site_url = self.config["site_url"]
        username = self.config["username"]
        password = self.config["password"]

        # 先检查是否已登录
        try:
            await self.page.goto(site_url, wait_until='domcontentloaded', timeout=15000)
            await asyncio.sleep(1)
            text = await self.page.evaluate('() => document.body?.innerText?.substring(0, 500) || ""')
            if '退出登录' in text or '余额' in text:
                log.info("✅ Session有效，跳过登录")
                self.logged_in = True
                return True
        except:
            pass

        log.info(f"🔐 登录中...")
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

        # 点击登录
        await self.page.evaluate('''() => {
            document.querySelectorAll('input[type="button"]').forEach(btn => {
                if (btn.value?.includes('登录') && btn.offsetParent !== null) btn.click();
            });
        }''')
        await asyncio.sleep(4)

        # 关弹窗
        await self.page.evaluate('() => { if(typeof layer !== "undefined") layer.closeAll(); }')
        
        # 验证
        text = await self.page.evaluate('() => document.body?.innerText || ""')
        if '余额' in text or '退出登录' in text:
            log.info("✅ 登录成功")
            self.logged_in = True
            return True
        
        log.warning("⚠️ 登录状态不确定，停止后续流程")
        self.logged_in = False
        return False

    async def navigate_to_sports(self):
        """导航到体育页面"""
        await self.page.goto('https://18.167.221.88:48356/play/89', wait_until='domcontentloaded', timeout=30000)
        await asyncio.sleep(5)
        
        text = await self.page.evaluate('() => document.body?.innerText || ""')
        if '体育' in text or '滚球' in text or '足球' in text:
            log.info("⚽ 已进入体育页面")
            return True
        
        log.warning("⚠️ 体育页面内容未完全加载")
        return True

    async def get_sports_frame(self):
        """获取体育iframe，找不到则刷新"""
        for f in self.page.frames:
            if 'pc.9a877' in f.url:
                return f
        
        log.warning("⚠️ iframe丢失，刷新页面")
        await self.page.reload(wait_until='domcontentloaded', timeout=15000)
        await asyncio.sleep(3)
        
        for f in self.page.frames:
            if 'pc.9a877' in f.url:
                return f
        
        return None

    async def get_balance(self):
        """获取余额"""
        for f in self.page.frames:
            if 'pc.9a877' not in f.url:
                continue
            try:
                result = await f.evaluate('''() => {
                    const els = document.querySelectorAll('.amount.font-din, .balance-amount-title');
                    for (const el of els) {
                        const nums = el.innerText?.match(/[\\d.]+/g);
                        if (nums) {
                            const val = parseFloat(nums[0]);
                            if (val > 0 && val < 1000000) return val;
                        }
                    }
                    return null;
                }''')
                if result:
                    self.balance = result
                    return result
            except:
                continue
        return self.balance

    # ==================== 快速扫描 ====================

    async def scan_matches(self):
        """
        一次性扫描所有比赛，返回结构化数据
        关键：只遍历一次DOM，所有信息一次提取
        """
        frame = await self.get_sports_frame()
        if not frame:
            return []
        
        try:
            matches = await frame.evaluate('''() => {
                const results = [];
                const items = document.querySelectorAll('.home-match-info');
                
                // 建立联赛映射
                const leagueMap = new Map();
                const obGroups = document.querySelectorAll('.matches-group-league, .home-match-list__group');
                for (const g of obGroups) {
                    const le = g.querySelector('.league-name');
                    const league = le?.innerText?.trim()?.split("\\n")[0] || '';
                    g.querySelectorAll('.home-match-info').forEach(item => leagueMap.set(item, league));
                }
                
                for (const item of items) {
                    try {
                        const teams = item.querySelectorAll('.team-name');
                        if (teams.length < 2) continue;
                        
                        const home = teams[0]?.innerText?.trim();
                        const away = teams[1]?.innerText?.trim();
                        if (!home || !away) continue;
                        if (home.includes('(') || away.includes('(')) continue;  // 过滤虚拟
                        
                        const league = leagueMap.get(item) || '';
                        
                        // 时间和比分
                        const te = item.querySelector('.match-left-time');
                        const se = item.querySelector('.match-left-text');
                        const timeStr = te?.innerText?.trim() || '';
                        const status = se?.innerText?.trim() || '';
                        
                        let minute = 0;
                        const tm = timeStr.match(/(\d{1,3})/);
                        if (tm) minute = parseInt(tm[1]);
                        
                        const scoreEls = item.querySelectorAll('.match-score span');
                        let homeScore = 0, awayScore = 0;
                        if (scoreEls.length >= 2) {
                            homeScore = parseInt(scoreEls[0]?.innerText?.trim()) || 0;
                            awayScore = parseInt(scoreEls[1]?.innerText?.trim()) || 0;
                        }
                        
                        // 大小球赔率
                        const totalBox = item.querySelector('.match-full-odds-total');
                        let ouLine = null, overOdds = null, underOdds = null;
                        if (totalBox && !totalBox.querySelector('.is-lock')) {
                            const lineSpans = totalBox.querySelectorAll('span');
                            for (const s of lineSpans) {
                                const t = s.innerText?.trim() || '';
                                if (t.startsWith('大') || t.startsWith('O')) {
                                    ouLine = t.replace(/^[大O]\s*/, '').trim();
                                    break;
                                }
                                if (t.startsWith('小') || t.startsWith('U')) {
                                    // 盘口可能在这里
                                }
                            }
                            // 如果没找到，从 line text 取
                            if (!ouLine) {
                                const lineText = totalBox.querySelector('.line-text, .odds-line');
                                if (lineText) ouLine = lineText.innerText?.trim() || null;
                            }
                            
                            const vals = totalBox.querySelectorAll('.value');
                            if (vals.length >= 2) {
                                overOdds = parseFloat(vals[0]?.innerText?.trim());
                                underOdds = parseFloat(vals[1]?.innerText?.trim());
                                if (isNaN(overOdds)) overOdds = null;
                                if (isNaN(underOdds)) underOdds = null;
                            }
                        }
                        
                        if (overOdds !== null || underOdds !== null) {
                            results.push({
                                home, away, league, minute, status,
                                homeScore, awayScore,
                                market: 'total',
                                ouLine,
                                overOdds, underOdds
                            });
                        }
                    } catch(e) {}
                }
                
                return results;
            }''')
            return matches or []
        except Exception as e:
            log.error(f"扫描异常: {e}")
            return []

    # ==================== 快速投注 ====================

    async def fast_bet(self, match_info, bet_side, bet_amount, drift_limit_pct=15.0):
        """
        快速投注 — 人类流程
        
        match_info: scan_matches 返回的一场比赛
        bet_side: 'over' 或 'under'
        bet_amount: 投注金额
        """
        home = match_info['home']
        away = match_info['away']
        scan_odds = match_info.get(f'{bet_side}Odds')
        simulation = self.config.get("simulation_mode", True)
        
        if not scan_odds:
            return False, {"error": "no_odds"}
        
        result = {"scan_odds": scan_odds, "click_odds": None, "confirm_odds": None}
        
        frame = await self.get_sports_frame()
        if not frame:
            return False, {"error": "no_frame"}
        
        # === 策略：先点击比赛名打开右侧详情，然后在右侧点赔率 ===
        # OP7的设计通常是：左边点比赛 -> 右边出盘口 -> 点右边盘口 -> 弹出投注单
        click = await frame.evaluate('''
            async (p) => {
                const {home, away, betSide} = p;
                
                // 1. 先找比赛并点击 (打开右侧面板)
                const items = document.querySelectorAll('.home-match-info');
                let target = null;
                for (const item of items) {
                    const teams = item.querySelectorAll('.team-name');
                    if (teams.length < 2) continue;
                    if (teams[0]?.innerText?.trim() === home && teams[1]?.innerText?.trim() === away) {
                        target = item;
                        // 点击队名区域以激活右侧面板
                        const teamContainer = item.querySelector('.team-container, .teams-container, .home-away-container');
                        if (teamContainer) teamContainer.click();
                        else item.click();
                        break;
                    }
                }
                if (!target) return {ok: false, err: 'match_not_found'};
                
                // 等待右侧面板加载
                await new Promise(r => setTimeout(r, 500));
                
                // 2. 在右侧面板找盘口
                let box = null;
                const allBoxes = document.querySelectorAll('.match-full-odds-total');
                for (const b of allBoxes) {
                    // 必须包含 .value 元素才是有赔率的区域
                    if (b.querySelector('.value') && b.offsetParent !== null) {
                        box = b;
                        break;
                    }
                }
                
                if (!box) return {ok: false, err: 'no_total_box'};
                
                const oddsContainers = box.querySelectorAll('.team-odds-item');
                const idx = betSide === 'over' ? 0 : 1;
                
                let clickTarget = oddsContainers[idx];
                if (!clickTarget) {
                    // 回退：找第几个 .value-wrap
                    const wraps = box.querySelectorAll('.value-wrap');
                    clickTarget = wraps[idx];
                }
                if (!clickTarget) return {ok: false, err: 'no_odds_val'};
                
                // 读取赔率
                const oddsEl = clickTarget.querySelector('.value');
                const odds = parseFloat(oddsEl ? oddsEl.innerText?.trim() : clickTarget.innerText?.trim());
                if (!odds || isNaN(odds)) return {ok: false, err: 'parse_fail'};
                
                // 模拟完整鼠标事件 (更像人，触发Vue/React监听器)
                clickTarget.dispatchEvent(new MouseEvent('mousedown', {bubbles: true}));
                clickTarget.dispatchEvent(new MouseEvent('mouseup', {bubbles: true}));
                clickTarget.click();
                
                return {ok: true, odds};
            }
        ''', {"home": home, "away": away, "betSide": bet_side})
        
        if not click.get('ok'):
            return False, {"error": click.get('err', 'click_fail')}
        
        result["click_odds"] = click["odds"]
        
        # === 检查漂移 ===
        drift = ((result["click_odds"] - result["scan_odds"]) / result["scan_odds"]) * 100
        result["drift_pct"] = drift
        if drift < -drift_limit_pct:
            log.warning(f"⚠️ 漂移 {drift:+.1f}% 超阈值")
            # 关闭面板
            await frame.evaluate('() => document.body.click()')
            return False, {"error": "drift_rejected", "drift_pct": drift}
        
        if simulation:
            log.info(f"🔵 模拟: {home} vs {away} {bet_side} @ {result['click_odds']:.3f} (漂移{drift:+.1f}%)")
            result["action"] = "simulated"
            return True, result
        
        # === 真实投注：输入金额+确认 ===
        confirm = await frame.evaluate('''
            async (amount) => {
                // 1. 等输入框出现（轮询，最多1秒）
                let input = null;
                for (let i = 0; i < 10; i++) {
                    input = document.querySelector('input.input-value');
                    if (input && input.offsetParent !== null) break;
                    await new Promise(r => setTimeout(r, 100));
                }
                if (!input) return {ok: false, err: 'no_input'};
                
                // 2. 输入金额
                input.focus();
                input.select();
                input.value = amount.toString();
                input.dispatchEvent(new Event('input', {bubbles: true}));
                input.dispatchEvent(new Event('change', {bubbles: true}));
                
                // 3. 找确认按钮
                let btn = null;
                for (let i = 0; i < 10; i++) {
                    const btns = document.querySelectorAll('button, .btn-confirm, .bet-confirm');
                    for (const b of btns) {
                        if (b.offsetParent === null) continue;
                        const txt = (b.innerText || '').trim();
                        if ((txt.includes('确认') || txt.includes('Confirm')) && 
                            !b.disabled && !b.classList.contains('disabled')) {
                            btn = b;
                            break;
                        }
                    }
                    if (btn) break;
                    await new Promise(r => setTimeout(r, 100));
                }
                if (!btn) return {ok: false, err: 'no_confirm'};
                
                // 4. 点击确认
                btn.click();
                
                // 5. 等结果（最多3秒）
                for (let i = 0; i < 30; i++) {
                    const txt = document.body?.innerText || '';
                    if (txt.includes('投注成功') || txt.includes('Success')) 
                        return {ok: true, msg: 'success'};
                    if (txt.includes('投注失败') || txt.includes('无效') || txt.includes('Fail'))
                        return {ok: false, msg: 'failed'};
                    await new Promise(r => setTimeout(r, 100));
                }
                return {ok: false, err: 'result_timeout'};
            }
        ''', bet_amount)
        
        if not confirm.get('ok'):
            return False, {"error": confirm.get('err', 'confirm_fail')}
        
        result["action"] = confirm.get('msg', 'placed')
        result["confirm_odds"] = result["click_odds"]
        if result["action"] != 'success':
            return False, {"error": f"unexpected_result:{result['action']}", **result}
        
        if result["action"] == 'success':
            log.info(f"✅ 投注成功! {home} vs {away} {bet_side} @ {result['confirm_odds']:.3f}")
        else:
            log.info(f"✅ 已确认 {home} vs {away} {bet_side} @ {result['confirm_odds']:.3f} ({result['action']})")
        
        return True, result


# ==================== 策略引擎 ====================

def parse_ou_line(s):
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


def evaluate_strategy(match, strategy_config):
    """
    策略评估 — 同步函数，不依赖asyncio
    返回: (should_bet, bet_side, reason)
    """
    strat = strategy_config.get("strategy", {})
    name = strat.get("name", "seven_goal_line")
    
    over_odds = match.get("overOdds")
    under_odds = match.get("underOdds")
    minute = match.get("minute", 0)
    status = match.get("status", "")
    h_score = match.get("homeScore", 0)
    a_score = match.get("awayScore", 0)
    total_goals = (h_score or 0) + (a_score or 0)
    ou_line = parse_ou_line(match.get("ouLine", ""))
    
    in_progress = any(kw in status for kw in ['上半场', '下半场', 'HT', '1H', '2H']) or (minute > 0 and minute < 90)
    
    if name == "seven_goal_line":
        return eval_seven_goal_line(match, ou_line, total_goals, under_odds, in_progress, minute, strat)
    elif name == "big_ou_late":
        return eval_big_ou_late(match, ou_line, total_goals, under_odds, in_progress, minute, strat)
    
    return False, None, "no_strategy"


def eval_seven_goal_line(match, ou_line, total_goals, under_odds, in_progress, minute, strat):
    if ou_line < 6.875 or ou_line > 7.125:
        return False, None, f"ou{ou_line:.2f}"
    if total_goals < 6:
        return False, None, f"goals{total_goals}"
    if not in_progress:
        return False, None, "not_live"
    
    threshold = strat.get("under_threshold", 0.70)
    if under_odds and under_odds >= threshold:
        return True, "under", f"⭐ 7球卡点买小 @ {under_odds:.3f} (盘{ou_line:.2f} 球{total_goals})"
    return False, None, f"odds{under_odds or 0:.3f}<{threshold}"


def eval_big_ou_late(match, ou_line, total_goals, under_odds, in_progress, minute, strat):
    if ou_line < 6.5:
        return False, None, f"ou{ou_line:.1f}"
    if minute < 75 or minute > 87:
        return False, None, f"min{minute}"
    if not in_progress:
        return False, None, "not_live"
    
    threshold = strat.get("under_threshold", 1.8)
    if under_odds and under_odds >= threshold:
        return True, "under", f"⭐ 大盘买小 @ {under_odds:.2f} (75-87min)"
    return False, None, f"odds{under_odds or 0:.2f}<{threshold}"


# ==================== 主循环 ====================

async def main():
    config = load_config()
    db = init_db()
    browser = FBSportsBrowser(config)
    
    simulation = config.get("simulation_mode", True)
    mode_str = "🔵 模拟" if simulation else "🔴 实盘"
    log.info(f"{'='*50}")
    log.info(f"FB体育 v4 快速投注 — {mode_str}")
    log.info(f"策略: {config['strategy']['name']}")
    log.info(f"漂移限制: 15%")
    log.info(f"{'='*50}")
    
    # 启动
    await browser.start()
    if not await browser.login():
        log.error("登录失败，退出")
        await browser.stop()
        db.close()
        return
    await browser.navigate_to_sports()
    
    balance = await browser.get_balance()
    log.info(f"💰 余额: ¥{balance}" if balance else "💰 余额获取失败")
    
    scan_count = 0
    daily_bets = 0
    max_daily_bets = config["strategy"].get("max_daily_bets", 5)
    max_daily_loss = config["strategy"].get("max_daily_loss", 300)
    
    try:
        while True:
            scan_count += 1
            now = datetime.now()
            
            # 风控
            if daily_bets >= max_daily_bets:
                log.info(f"⛔ 今日已达最大投注 {max_daily_bets}")
                await asyncio.sleep(300)
                continue
            
            # 扫描
            matches = await browser.scan_matches()
            if not matches:
                log.info(f"🔍 #{scan_count} [{now.strftime('%H:%M')}] 无比赛")
                await asyncio.sleep(30)
                continue
            
            log.info(f"🔍 #{scan_count} [{now.strftime('%H:%M')}] {len(matches)}场")
            
            # 策略评估
            signals = []
            for m in matches:
                should_bet, bet_side, reason = evaluate_strategy(m, config)
                if should_bet:
                    signals.append((m, bet_side, reason))
            
            # 执行投注
            for match, bet_side, reason in signals:
                match_key = f"{match['home']}_{match['away']}_{bet_side}"
                if match_key in browser._bet_history:
                    continue
                
                log.info(f"🎯 {reason}")
                
                bet_amount = config["strategy"].get("bet_amount", 100)
                success, details = await browser.fast_bet(match, bet_side, bet_amount)
                
                record_signal(db,
                    timestamp=now.isoformat(),
                    league=match.get('league', ''),
                    home=match['home'],
                    away=match['away'],
                    bet_side=bet_side,
                    market=match.get('market', 'total'),
                    scan_odds=details.get('scan_odds'),
                    click_odds=details.get('click_odds'),
                    confirm_odds=details.get('confirm_odds'),
                    odds_drift_pct=details.get('drift_pct'),
                    bet_amount=bet_amount if success else 0,
                    action=details.get('action', 'unknown'),
                    simulation=1 if simulation else 0,
                    notes=reason
                )
                
                if success:
                    browser._bet_history.add(match_key)
                    daily_bets += 1
            
            # 短间隔扫描
            await asyncio.sleep(30)
    
    except KeyboardInterrupt:
        log.info("\n🛑 手动停止")
    except Exception as e:
        log.error(f"💥 错误: {e}", exc_info=True)
    finally:
        await browser.stop()
        db.close()
        log.info("系统已关闭")


if __name__ == "__main__":
    asyncio.run(main())

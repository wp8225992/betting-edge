#!/usr/bin/env python3
"""
FB体育自动投注控制器 - 常驻浏览器模式
架构：
  1. 登录常驻 - 浏览器打开后不动，反复用
  2. 自动扫描 - 循环扫DOM，策略匹配自动下注
  3. 手动指挥 - 接收指令下注
  4. 登出检测 - 检测到被登出自动重新登录
"""

import asyncio
import json
import logging
import os
import sys
import re
import signal
from datetime import datetime, timedelta
from pathlib import Path

from playwright.async_api import async_playwright

# ==================== 配置 ====================

SCRIPT_DIR = Path(__file__).parent
SITE_URL = 'https://18.167.221.88:48356/play/89'
USERNAME = 'll0602'
PASSWORD = 'wp8649562'
BROWSER_DIR = SCRIPT_DIR / 'browser_session_auto'
LOG_PATH = SCRIPT_DIR / 'controller.log'
COMMAND_FILE = Path.home() / '.hermes' / 'bet_command.json'
RESPONSE_FILE = Path.home() / '.hermes' / 'bet_response.json'

SCAN_INTERVAL = 30  # 秒
BET_AMOUNT = 10  # 默认投注金额
PID_FILE = SCRIPT_DIR / 'controller.pid'

# 策略配置
OU_LINE_RANGE = (6.625, 7.625)  # 6.5/7, 7, 7/7.5, 7.25, 7.5
UNDER_ODDS_MIN = 0.50

# 日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(str(LOG_PATH), encoding='utf-8'),
        logging.StreamHandler()
    ]
)
log = logging.getLogger('controller')

# 运行标志
running = True

def signal_handler(sig, frame):
    global running
    running = False
    log.info('收到停止信号，准备关闭...')

signal.signal(signal.SIGTERM, signal_handler)
signal.signal(signal.SIGINT, signal_handler)

# ==================== 浏览器管理 ====================

class BrowserManager:
    """管理常驻浏览器实例"""
    
    def __init__(self):
        self.pw = None
        self.context = None
        self.page = None
        self.frame = None
        self.balance = None
        self.logged_in = False
        self.last_login_time = None
    
    async def start(self):
        """启动浏览器"""
        # 清理锁文件
        for lock in ['SingletonLock', 'SingletonCookie', 'SingletonSocket']:
            p = BROWSER_DIR / lock
            if p.is_symlink() or p.exists():
                p.unlink(missing_ok=True)
        
        self.pw = await async_playwright().start()
        self.context = await self.pw.chromium.launch_persistent_context(
            str(BROWSER_DIR),
            headless=False,
            args=['--no-sandbox', '--disable-dev-shm-usage'],
            viewport={'width': 1280, 'height': 800},
        )
        self.page = self.context.pages[0] if self.context.pages else await self.context.new_page()
        log.info('浏览器已启动')
    
    async def login(self):
        """登录FB体育"""
        try:
            await self.page.goto(SITE_URL, wait_until='domcontentloaded', timeout=20000)
            await asyncio.sleep(3)
            
            body = await self.page.inner_text('body')
            if '退出登录' in body or '余额' in body:
                log.info('✅ Session有效，跳过登录')
                await asyncio.sleep(10)  # 等iframe
                await self.ensure_frame()
                return True
        except:
            pass
        
        log.info('🔐 登录中...')
        await self.page.goto(SITE_URL, wait_until='domcontentloaded', timeout=30000)
        await asyncio.sleep(2)
        
        # 填账号密码
        await self.page.evaluate('''(p) => {
            document.querySelectorAll('input').forEach(inp => {
                const ph = (inp.placeholder || '').toLowerCase();
                if (ph.includes('用户') || ph.includes('username')) {
                    inp.value = p.username;
                    inp.dispatchEvent(new Event('input', {bubbles: true}));
                }
                if (ph.includes('密码') || ph.includes('pass') || inp.type === 'password') {
                    inp.value = p.password;
                    inp.dispatchEvent(new Event('input', {bubbles: true}));
                }
            });
        }''', {'username': USERNAME, 'password': PASSWORD})
        await asyncio.sleep(0.5)
        
        # 点击登录
        await self.page.evaluate('''() => {
            document.querySelectorAll('input[type="button"]').forEach(btn => {
                if (btn.value?.includes('登录') && btn.offsetParent !== null) btn.click();
            });
        }''')
        await asyncio.sleep(5)
        
        # 刷新 → 跳首页 → 重新goto
        try:
            await self.page.reload(wait_until='domcontentloaded', timeout=15000)
        except:
            pass
        await asyncio.sleep(3)
        await self.page.goto(SITE_URL, wait_until='domcontentloaded', timeout=15000)
        await asyncio.sleep(10)  # 等iframe
        
        # 关弹窗
        await self.page.evaluate('() => { if(typeof layer !== "undefined") layer.closeAll(); }')
        
        # 验证
        body = await self.page.inner_text('body')
        if '退出登录' in body or '余额' in body:
            log.info('✅ 登录成功')
            self.logged_in = True
            self.last_login_time = datetime.now()
            await self.ensure_frame()
            return True
        else:
            log.warning('⚠️ 登录状态不确定')
            return False
    
    async def ensure_frame(self):
        """确保体育iframe已加载"""
        if self.frame:
            try:
                txt = await self.frame.inner_text('body')
                if len(txt) > 100:
                    return True
            except:
                self.frame = None
        
        for f in self.page.frames:
            if '9a877' in f.url or 'pc.' in f.url:
                self.frame = f
                return True
        
        # 强制刷新
        try:
            await self.page.goto(SITE_URL, wait_until='domcontentloaded', timeout=15000)
            await asyncio.sleep(10)
            for f in self.page.frames:
                if '9a877' in f.url or 'pc.' in f.url:
                    self.frame = f
                    return True
        except:
            pass
        
        return False
    
    async def check_logged_out(self):
        """检测是否被登出"""
        try:
            body = await self.page.inner_text('body')
            if '退出登录' not in body and '余额' not in body:
                log.warning('⚠️ 检测到可能已登出')
                self.logged_in = False
                return True
            return False
        except:
            return True
    
    async def relogin(self):
        """重新登录"""
        log.info('🔄 检测到登出，重新登录...')
        self.logged_in = False
        self.frame = None
        return await self.login()
    
    async def get_balance(self):
        """获取体育账户余额（必须在iframe加载后调用）"""
        try:
            if not self.frame:
                if not await self.ensure_frame():
                    return None
            
            body_text = await self.frame.inner_text('body')
            m = re.search(r'余额[：:]\s*([0-9,]+\.[0-9]+)', body_text)
            if m:
                return float(m.group(1).replace(',', ''))
            
            # 备用：找小数金额
            nums = re.findall(r'([0-9]+\.[0-9]{2})', body_text)
            for n in nums:
                val = float(n)
                if 1 < val < 10000:
                    return val
            return None
        except Exception as e:
            log.warning(f'余额读取失败: {e}')
            return None
    
    async def scan_matches(self):
        """扫描当前滚球比赛"""
        if not await self.ensure_frame():
            return []
        
        try:
            # 滚动加载
            await self.frame.evaluate("""
                async () => {
                    for (let i = 0; i < 15; i++) {
                        window.scrollTo(0, i * 500);
                        await new Promise(r => setTimeout(r, 200));
                    }
                    window.scrollTo(0, 0);
                }
            """)
            await asyncio.sleep(1)
            
            matches = await self.frame.evaluate("""
                () => {
                    const items = document.querySelectorAll('.home-match-info');
                    const results = [];
                    for (const item of items) {
                        const text = item.innerText || '';
                        const lines = text.split('\\n').map(l => l.trim());
                        
                        // 找比分
                        let homeScore = null, awayScore = null;
                        for (const line of lines) {
                            const m = line.match(/^(\\d)\\s*-\\s*(\\d)$/);
                            if (m) { homeScore = parseInt(m[1]); awayScore = parseInt(m[2]); break; }
                        }
                        if (homeScore === null) continue;
                        if (homeScore + awayScore > 15) continue; // 排除篮球
                        
                        // 联赛
                        let league = '';
                        const parentLeague = item.closest('.matches-group-league')?.querySelector('.league-name');
                        if (parentLeague) league = parentLeague.innerText?.trim() || '';
                        
                        // 状态/时间
                        const statusEl = item.querySelector('.match-left-text');
                        const timeEl = item.querySelector('.match-left-time');
                        const status = statusEl?.innerText?.trim() || '';
                        const time = timeEl?.innerText?.trim() || '';
                        
                        // 球队
                        const teamEls = item.querySelectorAll('.team-name');
                        const teams = Array.from(teamEls).map(e => e.innerText?.trim()).filter(Boolean);
                        
                        // 赔率
                        const oddsBox = item.querySelector('.match-full-odds-total');
                        const oddsText = oddsBox?.innerText || '';
                        // 解析盘口和赔率
                        const ouMatch = oddsText.match(/([\\d.]+\\/\\d+|\\d+\\.\\d+)/);
                        const overMatch = oddsText.match(/大\\s*([\\d.]+)/);
                        const underMatch = oddsText.match(/小\\s*([\\d.]+)/);
                        
                        results.push({
                            score: `${homeScore}-${awayScore}`,
                            total: homeScore + awayScore,
                            homeScore, awayScore,
                            league: league.substring(0, 50),
                            status, time,
                            teams: teams.slice(0, 2),
                            ouLine: ouMatch ? ouMatch[1] : '',
                            overOdds: overMatch ? parseFloat(overMatch[1]) : 0,
                            underOdds: underMatch ? parseFloat(underMatch[1]) : 0,
                        });
                    }
                    return results;
                }
            """)
            return matches
        except Exception as e:
            log.warning(f'扫描失败: {e}')
            return []
    
    async def place_bet(self, match_idx, side='under', amount=10):
        """对指定比赛下注"""
        if not await self.ensure_frame():
            return False, 'no_frame'
        
        try:
            # 1. 点击赔率
            click_result = await self.frame.evaluate("""
                (params) => {
                    const {idx, side} = params;
                    const items = document.querySelectorAll('.home-match-info');
                    if (idx >= items.length) return {ok: false, err: 'index_out_of_range'};
                    
                    const item = items[idx];
                    const oddsBox = item.querySelector('.match-full-odds-total');
                    if (!oddsBox) return {ok: false, err: 'no_odds_box'};
                    
                    const allSpans = oddsBox.querySelectorAll('*');
                    for (const el of allSpans) {
                        const txt = (el.innerText || '').trim();
                        const target = side === 'under' ? '小' : '大';
                        if (txt === target && el.offsetParent !== null) {
                            el.dispatchEvent(new MouseEvent('mousedown', {bubbles: true}));
                            el.dispatchEvent(new MouseEvent('mouseup', {bubbles: true}));
                            el.click();
                            return {ok: true, odds: txt};
                        }
                    }
                    return {ok: false, err: 'no_target_button'};
                }
            """, {'idx': match_idx, 'side': side})
            
            if not click_result.get('ok'):
                return False, click_result.get('err', 'click_failed')
            
            await asyncio.sleep(2)
            
            # 2. 设置金额
            await self.frame.evaluate("""
                (amount) => {
                    let input = document.querySelector('input.input-value');
                    if (!input) {
                        const inputs = document.querySelectorAll('input');
                        for (const inp of inputs) {
                            if (inp.offsetParent !== null && inp.type === 'text') {
                                input = inp; break;
                            }
                        }
                    }
                    if (!input) return {ok: false, err: 'no_input'};
                    
                    input.focus();
                    input.select();
                    const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
                    setter.call(input, amount.toString());
                    input.dispatchEvent(new Event('input', {bubbles: true}));
                    input.dispatchEvent(new Event('change', {bubbles: true}));
                    return {ok: true};
                }
            """, str(amount))
            await asyncio.sleep(1)
            
            # 3. 确认
            confirm_result = await self.frame.evaluate("""
                () => {
                    const allBtns = document.querySelectorAll('button, [class*="btn"], a');
                    for (const b of allBtns) {
                        if (b.offsetParent === null) continue;
                        const txt = (b.innerText || '').trim();
                        if ((txt.includes('确认') || txt.includes('Accept') || txt.includes('确定') || txt.includes('下注')) &&
                            !b.disabled && !txt.includes('教程')) {
                            b.click();
                            return {ok: true, btn: txt};
                        }
                    }
                    return {ok: false, err: 'no_confirm'};
                }
            """)
            
            if not confirm_result.get('ok'):
                return False, confirm_result.get('err', 'no_confirm')
            
            # 4. 等结果
            await asyncio.sleep(5)
            result_text = await self.frame.inner_text('body')
            if '投注成功' in result_text:
                return True, 'success'
            elif '投注失败' in result_text:
                return False, 'bet_failed'
            else:
                return True, 'clicked (result unclear)'
                
        except Exception as e:
            return False, f'exception: {e}'

# ==================== 指令处理 ====================

def read_command():
    """读取指令文件"""
    if not COMMAND_FILE.exists():
        return None
    try:
        with open(COMMAND_FILE, 'r') as f:
            cmd = json.load(f)
        # 读完后删除
        COMMAND_FILE.unlink(missing_ok=True)
        return cmd
    except:
        return None

def write_response(result):
    """写入响应"""
    try:
        with open(RESPONSE_FILE, 'w', encoding='utf-8') as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
    except:
        pass

async def handle_command(browser, cmd):
    """处理指令"""
    action = cmd.get('action', '')
    log.info(f'📥 收到指令: {action}')
    
    if action == 'balance':
        bal = await browser.get_balance()
        result = {'action': 'balance', 'balance': bal, 'time': datetime.now().strftime('%H:%M:%S')}
        log.info(f'💰 余额: ¥{bal}')
        write_response(result)
    
    elif action == 'scan':
        matches = await browser.scan_matches()
        result = {
            'action': 'scan',
            'count': len(matches),
            'matches': [
                {'score': m['score'], 'league': m['league'], 'status': m['status'],
                 'time': m['time'], 'ou': m['ouLine'], 'under': m['underOdds']}
                for m in matches
            ],
            'time': datetime.now().strftime('%H:%M:%S')
        }
        log.info(f'📊 扫描到 {len(matches)} 场比赛')
        write_response(result)
    
    elif action == 'bet':
        match_idx = cmd.get('idx', 0)
        side = cmd.get('side', 'under')
        amount = cmd.get('amount', BET_AMOUNT)
        
        if amount == 'all':
            bal = await browser.get_balance()
            amount = int(bal) if bal and bal > 10 else BET_AMOUNT
        
        success, result_msg = await browser.place_bet(match_idx, side, amount)
        result = {
            'action': 'bet',
            'success': success,
            'message': result_msg,
            'amount': amount,
            'time': datetime.now().strftime('%H:%M:%S')
        }
        log.info(f'{"✅" if success else "❌"} 下注: {result_msg}')
        write_response(result)
    
    elif action == 'relogin':
        success = await browser.relogin()
        result = {'action': 'relogin', 'success': success, 'time': datetime.now().strftime('%H:%M:%S')}
        write_response(result)
    
    else:
        result = {'action': action, 'error': 'unknown command'}
        write_response(result)

# ==================== 策略判断 ====================

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

def check_strategy(match):
    """检查是否匹配策略"""
    ou = parse_ou_line(match.get('ouLine', ''))
    under_odds = match.get('underOdds', 0)
    
    if ou < OU_LINE_RANGE[0] or ou > OU_LINE_RANGE[1]:
        return False
    if under_odds < UNDER_ODDS_MIN:
        return False
    
    return True

# ==================== 主循环 ====================

async def main():
    global running
    
    log.info('=' * 50)
    log.info('FB体育自动投注控制器启动')
    log.info('PID: %s', os.getpid())
    log.info('模式: 常驻浏览器 + 文件指令')
    log.info('=' * 50)
    
    # 写PID文件
    with open(PID_FILE, 'w') as f:
        f.write(str(os.getpid()))
    
    browser = BrowserManager()
    
    # 启动浏览器
    await browser.start()
    
    # 登录
    if not await browser.login():
        log.error('登录失败，退出')
        return
    
    # 首次读余额
    bal = await browser.get_balance()
    browser.balance = bal
    log.info(f'💰 体育账户余额: ¥{bal}')
    
    scan_count = 0
    
    log.info('🚀 进入主循环...')
    
    while running:
        try:
            scan_count += 1
            
            # 1. 检查指令
            cmd = read_command()
            if cmd:
                await handle_command(browser, cmd)
            
            # 2. 检测登出
            if await browser.check_logged_out():
                await browser.relogin()
            
            # 3. 自动扫描
            matches = await browser.scan_matches()
            
            if not matches:
                log.info(f'🔍 #{scan_count} [{datetime.now().strftime("%H:%M:%S")}] 无滚球比赛')
            else:
                football = [m for m in matches if '篮球' not in m.get('league', '')]
                log.info(f'🔍 #{scan_count} [{datetime.now().strftime("%H:%M:%S")}] {len(football)}场足球 / {len(matches)}场总计')
                
                # 策略匹配
                for i, m in enumerate(football):
                    if check_strategy(m):
                        log.info(f'🎯 策略匹配: {m["score"]} | {m["league"]} | 盘{m["ouLine"]} 小{m["underOdds"]}')
                        # 自动下注（可开关）
                        # success, msg = await browser.place_bet(i, 'under', BET_AMOUNT)
                        # log.info(f'  {"✅" if success else "❌"} {msg}')
            
            # 4. 定期更新余额
            if scan_count % 10 == 0:
                new_bal = await browser.get_balance()
                if new_bal and new_bal != browser.balance:
                    log.info(f'💰 余额变化: ¥{browser.balance} → ¥{new_bal}')
                    browser.balance = new_bal
            
            await asyncio.sleep(SCAN_INTERVAL)
            
        except asyncio.CancelledError:
            break
        except Exception as e:
            log.error(f'循环异常: {e}', exc_info=True)
            await asyncio.sleep(10)
    
    log.info('🛑 控制器关闭')
    try:
        await browser.context.close()
    except:
        pass
    try:
        await browser.pw.stop()
    except:
        pass
    PID_FILE.unlink(missing_ok=True)

if __name__ == '__main__':
    asyncio.run(main())

#!/usr/bin/env python3
"""快速投注 - 找足球比赛买小 全下"""
import asyncio, os, sys

os.environ['DISPLAY'] = ':99'
sys.path.insert(0, os.path.dirname(__file__))
from playwright.async_api import async_playwright

SITE_URL = 'https://18.167.221.88:48356/play/89'
USERNAME = 'll0602'
PASSWORD = 'wp8649562'

async def quick_bet():
    session_dir = './browser_session_auto'
    
    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            session_dir,
            headless=False,
            args=['--no-sandbox', '--disable-dev-shm-usage'],
            viewport={'width': 1280, 'height': 800},
        )
        page = context.pages[0] if context.pages else await context.new_page()
        
        print("🌐 打开FB体育...")
        await page.goto(SITE_URL, wait_until='domcontentloaded', timeout=20000)
        await asyncio.sleep(3)
        
        body = await page.inner_text('body')
        if '退出登录' not in body and '余额' not in body:
            print("🔐 登录中...")
            await page.fill('#loginName', USERNAME)
            await page.fill('#password', PASSWORD)
            await page.evaluate('''() => {
                document.querySelectorAll('input[type="button"]').forEach(btn => {
                    if (btn.value?.includes('登录') && btn.offsetParent !== null) btn.click();
                });
            }''')
            await asyncio.sleep(5)
            try:
                await page.reload(wait_until='domcontentloaded', timeout=15000)
            except:
                pass
            await asyncio.sleep(3)
            await page.goto(SITE_URL, wait_until='domcontentloaded', timeout=15000)
            await asyncio.sleep(10)
        
        print("⏳ 等待iframe...")
        frame = None
        for i in range(15):
            await asyncio.sleep(3)
            for f in page.frames:
                if '9a877' in f.url or 'pc.' in f.url:
                    txt = await f.inner_text('body')
                    if len(txt) > 1000 and ('足球' in txt or '滚球' in txt or 'LIVE' in txt):
                        frame = f
                        print(f"  ✅ iframe（第{i+1}次），{len(txt)}字")
                        break
            if frame:
                break
        
        if not frame:
            print("❌ iframe失败")
            await context.close()
            return
        
        # 滚动
        await frame.evaluate("""
            async () => {
                for (let i = 0; i < 15; i++) {
                    window.scrollTo(0, i * 500);
                    await new Promise(r => setTimeout(r, 200));
                }
                window.scrollTo(0, 0);
            }
        """)
        await asyncio.sleep(1)
        
        # 获取所有足球比赛 - 用联赛名过滤（排除篮球关键词）
        print("🔍 获取所有足球比赛...")
        matches = await frame.evaluate("""
            () => {
                const groups = document.querySelectorAll('.matches-group-league, .league-header-box, .home-match-info');
                const results = [];
                let currentLeague = '';
                
                const items = document.querySelectorAll('.home-match-info');
                for (const item of items) {
                    const text = item.innerText || '';
                    const lines = text.split('\\n').map(l => l.trim()).filter(l => l);
                    
                    // 找比分 - 足球比分通常≤9
                    let homeScore = null, awayScore = null;
                    for (const line of lines) {
                        const m = line.match(/^(\\d)\\s*-\\s*(\\d)$/);
                        if (m) {
                            homeScore = parseInt(m[1]);
                            awayScore = parseInt(m[2]);
                            break;
                        }
                    }
                    
                    if (homeScore === null) continue;
                    
                    // 足球过滤 - 总分不超过15
                    if (homeScore + awayScore > 15) continue;
                    
                    // 找联赛
                    let league = '';
                    const prevEl = item.previousElementSibling;
                    if (prevEl) {
                        const leagueEl = prevEl.querySelector('.league-name, .league-title');
                        if (leagueEl) league = leagueEl.innerText?.trim() || '';
                    }
                    // 也检查父元素
                    const parentLeague = item.closest('.matches-group-league')?.querySelector('.league-name');
                    if (parentLeague) league = parentLeague.innerText?.trim() || league;
                    
                    // 找状态和时间
                    const statusEl = item.querySelector('.match-left-text');
                    const timeEl = item.querySelector('.match-left-time');
                    const status = statusEl?.innerText?.trim() || '';
                    const time = timeEl?.innerText?.trim() || '';
                    
                    // 找球队
                    const teamEls = item.querySelectorAll('.team-name');
                    const teams = Array.from(teamEls).map(e => e.innerText?.trim()).filter(Boolean);
                    
                    results.push({
                        score: `${homeScore}-${awayScore}`,
                        total: homeScore + awayScore,
                        league: league.substring(0, 50),
                        status,
                        time,
                        teams: teams.slice(0, 2),
                    });
                }
                return results;
            }
        """)
        
        print(f"  找到 {len(matches)} 场足球比赛:")
        for m in matches[:20]:
            print(f"    {m['score']} | {m['league']} | {m['status']} {m['time']} | {m['teams']}")
        
        # 找5-2
        target = None
        for m in matches:
            if m['score'] == '5-2' or m['score'] == '2-5':
                target = m
                break
        
        if not target:
            print("\n  ❌ 没找到5-2的比赛")
            print("  高进球比赛:")
            for m in sorted(matches, key=lambda x: -x['total'])[:10]:
                print(f"    {m['score']} | {m['league']} | {m['status']}")
            await context.close()
            return
        
        print(f"\n🎯 找到5-2! {target}")
        
        # 投注逻辑...
        print("\n⚠️ 找到比赛但未自动下注（需确认）")
        
        await context.close()

asyncio.run(quick_bet())

#!/usr/bin/env python3
"""查看FB体育当前页面内容"""
import asyncio, os, sys

os.environ['DISPLAY'] = ':99'
sys.path.insert(0, os.path.dirname(__file__))
from playwright.async_api import async_playwright

SITE_URL = 'https://18.167.221.88:48356/play/89'
USERNAME = 'll0602'
PASSWORD = 'wp8649562'

async def view_page():
    session_dir = './browser_session_auto'
    
    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            session_dir,
            headless=False,
            args=['--no-sandbox', '--disable-dev-shm-usage'],
            viewport={'width': 1280, 'height': 800},
        )
        page = context.pages[0] if context.pages else await context.new_page()
        
        await page.goto(SITE_URL, wait_until='domcontentloaded', timeout=20000)
        await asyncio.sleep(3)
        
        body = await page.inner_text('body')
        if '退出登录' not in body and '余额' not in body:
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
        
        frame = None
        for f in page.frames:
            if '9a877' in f.url or 'pc.' in f.url:
                frame = f
                break
        
        if not frame:
            print("无iframe")
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
        
        # 输出所有联赛名+比分
        info = await frame.evaluate("""
            () => {
                const groups = document.querySelectorAll('.matches-group-league');
                const results = [];
                for (const g of groups) {
                    const league = g.querySelector('.league-name')?.innerText?.trim() || '';
                    const items = g.querySelectorAll('.home-match-info');
                    for (const item of items) {
                        const text = item.innerText || '';
                        // 提取比分
                        const lines = text.split('\\n').map(l => l.trim());
                        let score = '';
                        for (const line of lines) {
                            if (line.match(/^\\d+\\s*-\\s*\\d+$/)) {
                                score = line.replace(/\\s/g, '');
                                break;
                            }
                        }
                        // 提取时间/状态
                        const timeEl = item.querySelector('.match-left-time, .match-left-text');
                        const timeInfo = timeEl?.innerText?.trim() || '';
                        results.push({league, score, timeInfo, textSnippet: text.substring(0, 100)});
                    }
                }
                return results;
            }
        """)
        
        print(f"共 {len(info)} 场比赛:")
        football = [m for m in info if '篮球' not in m['league']]
        basketball = [m for m in info if '篮球' in m['league']]
        print(f"  足球: {len(football)}场")
        print(f"  篮球: {len(basketball)}场")
        
        print("\n足球比赛:")
        for m in football[:20]:
            print(f"  {m['league']}: {m['score']} ({m['timeInfo']})")
        
        print("\n篮球比赛:")
        for m in basketball[:10]:
            print(f"  {m['league']}: {m['score']} ({m['timeInfo']})")
        
        await context.close()

asyncio.run(view_page())

#!/usr/bin/env python3
"""查看比赛DOM结构"""
import asyncio, os, sys

os.environ['DISPLAY'] = ':99'
sys.path.insert(0, os.path.dirname(__file__))
from playwright.async_api import async_playwright

SITE_URL = 'https://18.167.221.88:48356/play/89'
USERNAME = 'll0602'
PASSWORD = 'wp8649562'

async def view():
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
        
        # 输出第一个足球match的HTML结构
        html = await frame.evaluate("""
            () => {
                const items = document.querySelectorAll('.home-match-info');
                if (items.length === 0) return 'NO_ITEMS';
                // 找第一个非篮球的
                for (const item of items) {
                    const text = item.innerText || '';
                    if ('篮球' in text.split('\\n').some(l => l.includes('篮球'))) continue;
                    // 跳过篮球相关的联赛
                    const leagueEl = item.closest('.matches-group-league')?.querySelector('.league-name');
                    if (leagueEl && leagueEl.innerText?.includes('篮球')) continue;
                    return item.innerHTML;
                }
                return items[0].innerHTML;
            }
        """)
        
        print("第一个足球match的HTML:")
        print(html[:2000])
        
        # 也输出文本
        text = await frame.evaluate("""
            () => {
                const items = document.querySelectorAll('.home-match-info');
                if (items.length === 0) return 'NO_ITEMS';
                for (const item of items) {
                    const leagueEl = item.closest('.matches-group-league')?.querySelector('.league-name');
                    if (leagueEl && leagueEl.innerText?.includes('篮球')) continue;
                    return item.innerText;
                }
                return items[0].innerText;
            }
        """)
        print("\n文本:")
        print(text[:500])
        
        await context.close()

asyncio.run(view())

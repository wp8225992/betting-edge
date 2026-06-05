#!/usr/bin/env python3
"""FB体育余额+注单查询 - 重试iframe加载"""
import asyncio
import os
import sys

os.environ['DISPLAY'] = ':99'

from playwright.async_api import async_playwright

SITE_URL = 'https://18.167.221.88:48356/play/89'
USERNAME = 'll0602'
PASSWORD = 'wp8649562'

async def query():
    session_dir = os.path.join(os.path.dirname(__file__), 'browser_session_auto')
    
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
        
        # 检查登录状态
        body_text = await page.inner_text('body')
        logged_in = '退出登录' in body_text or '余额' in body_text
        
        if not logged_in:
            print("🔐 登录中...")
            await page.fill('#loginName', USERNAME)
            await page.fill('#password', PASSWORD)
            await page.evaluate('''() => {
                document.querySelectorAll('input[type="button"]').forEach(btn => {
                    if (btn.value?.includes('登录') && btn.offsetParent !== null) btn.click();
                });
            }''')
            await asyncio.sleep(5)
            await page.reload(wait_until='domcontentloaded', timeout=15000)
        
        # 重试加载iframe
        print("⏳ 等待体育iframe加载...")
        sports_frame = None
        for i in range(20):
            await asyncio.sleep(3)
            for frame in page.frames:
                if '9a877' in frame.url or 'pc.' in frame.url:
                    sports_frame = frame
                    break
            if sports_frame:
                # 验证内容
                txt = await sports_frame.inner_text('body')
                if len(txt) > 1000 and ('足球' in txt or 'LIVE' in txt or '滚球' in txt):
                    print(f"✅ iframe加载成功（第{i+1}次尝试）")
                    break
            if i == 0:
                print("  首次尝试，等待内容加载...")
        
        if not sports_frame:
            print("❌ iframe加载失败")
            await context.close()
            return
        
        frame_text = await sports_frame.inner_text('body')
        
        # 余额
        import re
        m = re.search(r'余额[：:]\s*([0-9,]+\.[0-9]+)', frame_text)
        if m:
            print(f"💰 余额: ¥{float(m.group(1).replace(',', '')):.2f}")
        
        # 未结算注单
        print("\n【未结算注单】")
        try:
            btn = sports_frame.get_by_text('未结算', exact=False)
            if await btn.count() > 0:
                await btn.first.click()
                await asyncio.sleep(2)
                txt = await sports_frame.inner_text('body')
                if 'NO DATA' in txt or '暂无数据' in txt:
                    print("  暂无未结算注单")
                else:
                    for line in txt.split('\n'):
                        line = line.strip()
                        if any(k in line for k in ['小', '大', '让球', '@', '滚球', '注单']):
                            if 5 < len(line) < 200:
                                print(f"  {line}")
        except Exception as e:
            print(f"  查询失败: {e}")
        
        # 已结算 - 扫描全页面
        print("\n【已结算注单】")
        try:
            # 找"注单历史"按钮
            history_btn = page.get_by_text('注单历史', exact=False)
            if await history_btn.count() == 0:
                history_btn = sports_frame.get_by_text('注单历史', exact=False)
            
            if await history_btn.count() > 0:
                await history_btn.first.click()
                await asyncio.sleep(5)
                
                # 注单历史可能在overlay，扫描全页面
                full_text = await page.inner_text('body')
                frame_text2 = await sports_frame.inner_text('body')
                combined = full_text + '\n' + frame_text2
                
                bet_lines = []
                for line in combined.split('\n'):
                    line = line.strip()
                    if any(k in line for k in ['投注成功', '投注失败', '滚球', '大', '小', '让球', '赢', '输', '结算', '注单', '@']):
                        if 5 < len(line) < 200:
                            bet_lines.append(line)
                
                if bet_lines:
                    seen = set()
                    for line in bet_lines:
                        if line not in seen:
                            print(f"  {line}")
                            seen.add(line)
                else:
                    print("  暂无已结算注单")
            else:
                print("  未找到注单历史入口")
                # 尝试找其他入口
                all_btns = await page.evaluate('''() => {
                    const els = document.querySelectorAll('button, a, div, span');
                    const texts = [];
                    for (const el of els) {
                        const t = (el.innerText || '').trim();
                        if (t.includes('注') || t.includes('历史') || t.includes('记录') || t.includes('Bet') || t.includes('bet')) {
                            texts.push(t.substring(0, 50));
                        }
                    }
                    return [...new Set(texts)];
                }''')
                if all_btns:
                    print(f"  可能入口: {all_btns[:10]}")
        except Exception as e:
            print(f"  查询失败: {e}")
        
        await context.close()

asyncio.run(query())

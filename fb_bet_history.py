#!/usr/bin/env python3
"""FB体育注单历史详细查询 - 有头模式"""
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
        await asyncio.sleep(5)
        
        # 检查登录状态
        body_text = await page.inner_text('body')
        if '退出登录' not in body_text and '余额' not in body_text:
            print("🔐 需要登录...")
            await page.fill('#loginName', USERNAME)
            await page.fill('#password', PASSWORD)
            await page.evaluate('''() => {
                document.querySelectorAll('input[type="button"]').forEach(btn => {
                    if (btn.value?.includes('登录') && btn.offsetParent !== null) btn.click();
                });
            }''')
            await asyncio.sleep(5)
            await page.reload(wait_until='domcontentloaded', timeout=15000)
            await asyncio.sleep(10)
        
        # 找体育iframe
        sports_frame = None
        for frame in page.frames:
            if '9a877' in frame.url or 'pc.' in frame.url:
                sports_frame = frame
                break
        
        if not sports_frame:
            print("❌ 未找到体育iframe")
            await context.close()
            return
        
        print(f"✅ 体育iframe: {sports_frame.url[:60]}")
        
        # 查余额
        frame_text = await sports_frame.inner_text('body')
        import re
        m = re.search(r'余额[：:]\s*([0-9,]+\.[0-9]+)', frame_text)
        if m:
            balance = float(m.group(1).replace(',', ''))
            print(f"💰 体育账户余额: ¥{balance:.2f}")
        
        # 查注单 - 尝试多种方式
        print("\n📋 查询注单历史...")
        
        # 方式1: iframe内的"注单历史"按钮
        print("\n【方式1: iframe内注单历史】")
        try:
            history_btn = sports_frame.get_by_text('注单历史', exact=False)
            count = await history_btn.count()
            print(f"  找到按钮数: {count}")
            if count > 0:
                await history_btn.first.click()
                await asyncio.sleep(3)
                
                # 获取整个页面文本（包括overlay）
                full_text = await page.inner_text('body')
                frame_text2 = await sports_frame.inner_text('body')
                combined = full_text + '\n' + frame_text2
                
                # 找投注相关行
                bet_lines = []
                for line in combined.split('\n'):
                    line = line.strip()
                    if any(k in line for k in ['投注成功', '投注失败', '滚球', '大', '小', '让球', '赢', '输', '结算', '@', '注单']):
                        if len(line) > 3 and len(line) < 200:
                            bet_lines.append(line)
                
                if bet_lines:
                    print("  找到相关行:")
                    for line in bet_lines[:30]:
                        print(f"    {line}")
                else:
                    print("  未找到投注记录")
                    print("  页面内容片段:", frame_text2[:500])
        except Exception as e:
            print(f"  失败: {e}")
        
        # 方式2: 主页面导航栏的"注单历史"
        print("\n【方式2: 主页面注单历史】")
        try:
            # 找导航栏中的注单历史
            nav_btns = page.get_by_text('注单历史')
            count = await nav_btns.count()
            print(f"  主页面按钮数: {count}")
            
            if count == 0:
                # 尝试找所有可能的按钮
                all_texts = await page.evaluate('''() => {
                    const els = document.querySelectorAll('button, a, div, span');
                    const texts = [];
                    for (const el of els) {
                        const t = (el.innerText || '').trim();
                        if (t.includes('注单') || t.includes('历史') || t.includes('投注') || t.includes('Bet')) {
                            texts.push(t.substring(0, 50));
                        }
                    }
                    return [...new Set(texts)];
                }''')
                print(f"  相关文本: {all_texts[:20]}")
        except Exception as e:
            print(f"  失败: {e}")
        
        # 方式3: 扫描所有frame找投注相关文本
        print("\n【方式3: 全页面扫描投注关键词】")
        try:
            all_bet_text = []
            for frame in page.frames:
                try:
                    txt = await frame.inner_text('body')
                    for line in txt.split('\n'):
                        line = line.strip()
                        if any(k in line for k in ['投注成功', '投注失败', '滚球大', '滚球小', '注单', '结算', '赢半', '输半']):
                            if 5 < len(line) < 200:
                                all_bet_text.append(f"[frame: {frame.url[:40]}] {line}")
                except:
                    pass
            
            if all_bet_text:
                print("  找到投注相关:")
                for line in all_bet_text[:30]:
                    print(f"    {line}")
            else:
                print("  未找到投注记录")
        except Exception as e:
            print(f"  失败: {e}")
        
        await context.close()

asyncio.run(query())

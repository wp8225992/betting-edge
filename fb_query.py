#!/usr/bin/env python3
"""FB体育余额和注单查询 - 有头模式(Xvfb)，与人眼看到的一样"""
import asyncio
import os
import sys

os.environ['DISPLAY'] = ':99'
sys.path.insert(0, os.path.dirname(__file__))

from playwright.async_api import async_playwright

SITE_URL = 'https://18.167.221.88:48356/play/89'
USERNAME = 'll0602'
PASSWORD = 'wp8649562'

async def query(mode='balance'):
    """
    mode: 'balance' - 查余额
          'bets' - 查注单（未结算+已结算）
          'all' - 全部
    """
    session_dir = os.path.join(os.path.dirname(__file__), 'browser_session_auto')
    
    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            session_dir,
            headless=False,
            args=['--no-sandbox', '--disable-dev-shm-usage'],
            viewport={'width': 1280, 'height': 800},
        )
        page = context.pages[0] if context.pages else await context.new_page()
        
        # 1. 导航到体育页面
        print("🌐 打开FB体育...")
        try:
            await page.goto(SITE_URL, wait_until='domcontentloaded', timeout=20000)
        except:
            print("  页面加载慢，等待iframe...")
        
        await asyncio.sleep(5)
        
        # 2. 检查是否需要登录
        body_text = await page.inner_text('body')
        if '退出登录' not in body_text and '余额' not in body_text:
            print("🔐 需要登录...")
            try:
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
            except Exception as e:
                print(f"  登录失败: {e}")
        
        # 3. 找体育iframe
        sports_frame = None
        for frame in page.frames:
            if '9a877' in frame.url or 'pc.' in frame.url:
                sports_frame = frame
                break
        
        if not sports_frame:
            print("❌ 未找到体育iframe，页面可能未加载完成")
            print("页面文本前200字:", body_text[:200])
            await context.close()
            return
        
        print(f"✅ 找到体育iframe: {sports_frame.url[:60]}...")
        
        frame_text = await sports_frame.inner_text('body')
        
        # 4. 查余额
        if mode in ('balance', 'all'):
            import re
            # 方法1: 正则匹配"余额：XX.XX"
            m = re.search(r'余额[：:]\s*([0-9,]+\.[0-9]+)', frame_text)
            if m:
                balance = float(m.group(1).replace(',', ''))
                print(f"💰 体育账户余额: ¥{balance:.2f}")
            else:
                # 方法2: 找小数金额（1-10000范围）
                nums = re.findall(r'([0-9]+\.[0-9]{2})', frame_text)
                valid = [float(n) for n in nums if 1 < float(n) < 10000]
                if valid:
                    print(f"💰 体育账户余额: ¥{valid[0]:.2f}")
                else:
                    print("⚠️ 未找到余额（可能页面结构变化）")
                    # 输出相关行帮助调试
                    for line in frame_text.split('\n'):
                        if '余额' in line or 'balance' in line.lower() or 'CNY' in line:
                            print(f"  相关行: {line.strip()}")
        
        # 5. 查注单
        if mode in ('bets', 'all'):
            print("\n📋 查询注单...")
            
            # 未结算
            print("\n【未结算注单】")
            try:
                # 先找左侧的"未结算注单"按钮
                bet_history_btn = sports_frame.get_by_text('未结算', exact=False)
                if await bet_history_btn.count() > 0:
                    await bet_history_btn.click()
                    await asyncio.sleep(2)
                    
                    panel_text = await sports_frame.inner_text('body')
                    if 'NO DATA' in panel_text or '暂无数据' in panel_text:
                        print("  暂无未结算注单")
                    else:
                        # 提取注单信息
                        for line in panel_text.split('\n'):
                            line = line.strip()
                            if any(k in line for k in ['小', '大', '让球', '@', '滚球']):
                                print(f"  {line}")
            except Exception as e:
                print(f"  查询未结算失败: {e}")
            
            # 已结算
            print("\n【已结算注单】")
            try:
                # 找"注单历史"按钮 - 可能在主页面
                history_btn = page.get_by_text('注单历史', exact=False)
                if await history_btn.count() == 0:
                    history_btn = sports_frame.get_by_text('注单历史', exact=False)
                
                if await history_btn.count() > 0:
                    await history_btn.click()
                    await asyncio.sleep(3)
                    
                    # 注单历史可能在overlay中，扫描整个页面
                    all_text = await page.inner_text('body')
                    # 找包含投注关键词的行
                    found = False
                    for line in all_text.split('\n'):
                        line = line.strip()
                        if any(k in line for k in ['投注成功', '投注失败', '结算', '赢', '输', '小', '大', '@', '滚球']):
                            print(f"  {line}")
                            found = True
                    if not found:
                        print("  暂无已结算注单")
                else:
                    print("  未找到注单历史入口")
            except Exception as e:
                print(f"  查询已结算失败: {e}")
        
        await context.close()

if __name__ == '__main__':
    mode = sys.argv[1] if len(sys.argv) > 1 else 'all'
    asyncio.run(query(mode))

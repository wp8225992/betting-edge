#!/usr/bin/env python3
"""快速查询FB体育余额 - 使用持久化浏览器profile"""
import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))
from playwright.async_api import async_playwright

async def main():
    session_dir = os.path.join(os.path.dirname(__file__), 'browser_session_auto')

    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            session_dir,
            headless=True,
            args=['--no-sandbox', '--disable-dev-shm-usage'],
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        )
        page = context.pages[0]

        try:
            await page.goto('https://18.167.221.88:48356/play/89', wait_until='domcontentloaded', timeout=30000)
            await page.wait_for_timeout(10000)

            # 找体育iframe
            for frame in page.frames:
                if '9a877' in frame.url or 'pc.' in frame.url:
                    print(f"Found sports frame: {frame.url[:60]}")
                    balance = await frame.evaluate("""
                        () => {
                            const el = document.querySelector('.balance-amount-title, .amount.font-din, [class*="balance"]');
                            return el ? el.innerText.trim() : null;
                        }
                    """)
                    if balance:
                        print(f"✅ 体育账户余额: {balance}")
                    else:
                        # Try broader search
                        body = await frame.inner_text('body')
                        import re
                        # Look for decimal numbers near balance keywords
                        for line in body.split('\n'):
                            if '余额' in line or 'balance' in line.lower() or 'CNY' in line:
                                nums = re.findall(r'[\d,]+\.\d{2}', line)
                                if nums:
                                    print(f"✅ 体育账户余额: {nums[0]}")
                                    break
                        else:
                            print("⚠️ 未找到余额")
                    await context.close()
                    return

            print("⚠️ 未找到体育iframe，尝试主页面...")
            body = await page.inner_text('body')
            import re
            for line in body.split('\n'):
                if '余额' in line or 'balance' in line.lower():
                    nums = re.findall(r'[\d,]+\.\d{2}', line)
                    if nums:
                        print(f"余额: {nums[0]}")
            print("页面摘要:", body[:300])
        except Exception as e:
            print(f"❌ 查询失败: {e}")
        finally:
            await context.close()

asyncio.run(main())

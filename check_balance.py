#!/usr/bin/env python3
"""快速查询FB体育余额"""
import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))
from playwright.async_api import async_playwright

async def main():
    session_dir = os.path.join(os.path.dirname(__file__), 'browser_session_auto')
    storage = os.path.join(session_dir, 'storage_state.json')
    if not os.path.exists(storage):
        print("❌ 登录状态不存在，需要先登录")
        return

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=['--no-sandbox']
        )
        context = await browser.new_context(
            storage_state=storage,
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        )
        page = await context.new_page()

        try:
            await page.goto('https://18.167.221.88:48356/play/89', wait_until='domcontentloaded', timeout=30000)
            await page.wait_for_timeout(5000)

            # 方法1: 尝试从frame中获取
            for frame in page.frames:
                try:
                    if '9a877' in frame.url or 'fb' in frame.url.lower():
                        balance = await frame.evaluate("""
                            () => {
                                const el = document.querySelector('.amount.font-din, .balance-amount-title, .user-money');
                                return el ? el.innerText.trim() : null;
                            }
                        """)
                        if balance:
                            print(f"✅ 余额: {balance}")
                            await browser.close()
                            return
                except:
                    continue

            # 方法2: 主页面扫描
            balance = await page.evaluate("""
                () => {
                    const els = document.querySelectorAll('[class*="balance"], [class*="amount"], [class*="money"]');
                    for (const el of els) {
                        const t = el.innerText.trim();
                        if (/[\d,]+\.\d{2}/.test(t)) return t;
                    }
                    return null;
                }
            """)
            if balance:
                print(f"✅ 余额: {balance}")
            else:
                print("⚠️ 未能获取余额，页面可能需要登录")
        except Exception as e:
            print(f"❌ 查询失败: {e}")
        finally:
            await browser.close()

asyncio.run(main())

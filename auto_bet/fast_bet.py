#!/usr/bin/env python3
"""
快速投注模块 — 人类流程优化版
核心思想：扫描→定位→点击→输入→确认一气呵成，不sleep、不重复遍历
"""

import asyncio
import logging

log = logging.getLogger("fast_bet")


async def fast_bet(frame, match_info, bet_side, bet_amount, drift_limit_pct=15.0):
    """
    快速投注流程 — 模拟人类操作速度
    
    match_info: 来自 scan_matches 的结果（已有赔率信息）
    bet_side: 'over'/'under'（大小球）或 'home'/'draw'/'away'（1X2）
    bet_amount: 投注金额
    drift_limit_pct: 最大可接受漂移百分比（默认15%，滚球波动大）
    
    返回: (success, result_info_dict)
    """
    home = match_info.get('home', '')
    away = match_info.get('away', '')
    market = match_info.get('market', 'total')  # 'total' 或 '1x2'
    
    # 扫描时的赔率
    if market == 'total':
        scan_odds = match_info.get(f'{bet_side}Odds')
    else:
        scan_odds = match_info.get(f'{bet_side}Odds')
    
    if not scan_odds:
        return False, {"error": "no_scan_odds"}
    
    result = {
        "scan_odds": scan_odds,
        "click_odds": None,
        "confirm_odds": None,
        "action": "pending"
    }
    
    # === 一步定位+点击+读取实时赔率 ===
    # 在同一个 JS evaluate 中完成：找比赛→点赔率→读实时赔率→开面板
    click_result = await frame.evaluate('''
        async (params) => {
            const {home, away, betSide, market} = params;
            
            // 1. 找比赛
            const items = document.querySelectorAll('.home-match-info');
            let target = null;
            for (const item of items) {
                const teams = item.querySelectorAll('.team-name');
                if (teams.length < 2) continue;
                const h = teams[0]?.innerText?.trim();
                const a = teams[1]?.innerText?.trim();
                if (h === home && a === away) {
                    target = item;
                    break;
                }
            }
            if (!target) return {ok: false, err: 'match_not_found'};
            
            // 2. 找赔率区域并点击
            let box, oddsIdx;
            if (market === 'total' || betSide === 'over' || betSide === 'under') {
                box = target.querySelector('.match-full-odds-total');
                oddsIdx = betSide === 'over' ? 0 : 1;
            } else if (betSide === 'home') {
                box = target.querySelector('.match-full-odds-setUp');
                oddsIdx = 0;
            } else if (betSide === 'draw') {
                box = target.querySelector('.match-full-odds-setUp');
                oddsIdx = 1;
            } else if (betSide === 'away') {
                box = target.querySelector('.match-full-odds-setUp');
                oddsIdx = 2;
            } else if (betSide === 'hdp_home') {
                box = target.querySelector('.match-full-odds-handicap');
                oddsIdx = 0;
            } else if (betSide === 'hdp_away') {
                box = target.querySelector('.match-full-odds-handicap');
                oddsIdx = 1;
            }
            
            if (!box) return {ok: false, err: 'market_box_not_found'};
            
            const items0 = box.querySelectorAll('.value');
            if (!items0[oddsIdx]) return {ok: false, err: 'odds_item_not_found'};
            
            // 读点击前的赔率
            const oddsBefore = parseFloat(items0[oddsIdx].innerText?.trim());
            if (!oddsBefore || isNaN(oddsBefore)) return {ok: false, err: 'odds_parse_fail'};
            
            // 点击
            items0[oddsIdx].click();
            
            return {ok: true, odds: oddsBefore};
        }
    ''', {"home": home, "away": away, "betSide": bet_side, "market": market})
    
    if not click_result.get('ok'):
        return False, {"error": click_result.get('err', 'unknown')}
    
    result["click_odds"] = click_result["odds"]
    
    # === 快速检查漂移 ===
    drift = ((result["click_odds"] - result["scan_odds"]) / result["scan_odds"]) * 100
    if drift < -drift_limit_pct:
        log.warning(f"漂移过大: {result['scan_odds']:.3f}→{result['click_odds']:.3f} ({drift:+.1f}%)")
        # 点开的赔率要关掉，等下次机会
        await frame.evaluate('() => document.body.click()')
        return False, {"error": "drift_rejected", "drift_pct": drift}
    
    # === 输入金额+确认 ===
    # OP7 投注面板是弹窗，input.input-value 是金额输入
    # 一步完成：输金额→点确认→读结果
    confirm_result = await frame.evaluate('''
        async (amount) => {
            // 1. 找金额输入框（等 DOM 稳定）
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
                const btns = document.querySelectorAll('button');
                for (const b of btns) {
                    if (b.offsetParent === null) continue;
                    const txt = (b.innerText || '').trim();
                    if (txt.includes('确认投注') || txt.includes('确认下注') || txt.includes('Confirm')) {
                        if (!b.disabled && !b.classList.contains('disabled')) {
                            btn = b;
                            break;
                        }
                    }
                }
                if (btn) break;
                await new Promise(r => setTimeout(r, 100));
            }
            if (!btn) return {ok: false, err: 'no_confirm_btn'};
            
            // 4. 点击确认
            btn.click();
            
            // 5. 等结果
            for (let i = 0; i < 30; i++) {
                const txt = document.body?.innerText || '';
                if (txt.includes('投注成功')) return {ok: true, msg: 'success'};
                if (txt.includes('投注失败') || txt.includes('无效')) return {ok: false, msg: 'failed'};
                await new Promise(r => setTimeout(r, 100));
            }
            
            // 超时但按钮点了，假设成功
            return {ok: true, msg: 'clicked_unknown_result'};
        }
    ''', bet_amount)
    
    if not confirm_result.get('ok'):
        return False, {"error": confirm_result.get('err', 'confirm_fail')}
    
    result["action"] = confirm_result.get('msg', 'placed')
    result["confirm_odds"] = result["click_odds"]  # 确认时赔率
    result["drift_pct"] = ((result["confirm_odds"] - result["scan_odds"]) / result["scan_odds"]) * 100
    
    return True, result

import time
from datetime import datetime
import config

from .context import TRADE_CONFIG, get_symbol_leverage
from . import state
from .okx import (
    get_current_position,
    get_open_orders_pending,
    get_algo_orders_pending,
    verify_tp_sl_against_pending,
    deduplicate_pending_tpsl,
    compute_aggressive_limit_price,
    btc_amount_to_okx_contracts,
    estimate_required_margin_usdt,
    set_position_tp_sl_for_okx,
    _parse_price_value,
    get_contract_size_btc,
    get_contract_size_btc_for_symbol,
    estimate_required_margin_usdt_for_symbol,
    get_all_positions,
    place_batch_orders,
    get_account_overview,
)
from .context import exchange
from .okx import _with_rate_limit_retry
from .state import set_symbol_tpsl_expected, ACCOUNT_POS_MODE, switch_active_symbol
from .utils import update_win_statistics, save_realized_pnl, append_trade_to_file


def execute_trade(signal_data, price_data):
    current_position = get_current_position()

    try:
        pending_orders = get_open_orders_pending(limit=50)
        print(f"下单前未成交普通订单数: {len(pending_orders)}")
        algo_orders = get_algo_orders_pending(limit=50)
        print(f"下单前策略委托(TP/SL)数: {len(algo_orders)}")
    except Exception:
        pass

    breakout_triggered = False
    try:
        if int(getattr(config, 'BREAKOUT_ENABLED', 1)) == 1 and price_data.get('levels_analysis'):
            upper = price_data['levels_analysis'].get('static_resistance')
            lower = price_data['levels_analysis'].get('static_support')
            px = float(price_data['price'])
            now_ts = time.time()
            if now_ts - state.last_breakout_ts >= int(getattr(config, 'BREAKOUT_COOLDOWN_SEC', 60)):
                if upper and upper > 0:
                    thresh = upper * (1.0 + float(getattr(config, 'BREAKOUT_UPPER_PCT', 0.003)) / 100.0)
                    if px >= thresh:
                        print("🚀 突破上轨触发：立即翻转为多头")
                        signal_data = dict(signal_data)
                        signal_data['signal'] = 'BUY'
                        signal_data['confidence'] = 'HIGH'
                        breakout_triggered = True
                if not breakout_triggered and lower and lower > 0:
                    thresh = lower * (1.0 - float(getattr(config, 'BREAKOUT_LOWER_PCT', 0.003)) / 100.0)
                    if px <= thresh:
                        print("📉 跌破下轨触发：立即翻转为空头")
                        signal_data = dict(signal_data)
                        signal_data['signal'] = 'SELL'
                        signal_data['confidence'] = 'HIGH'
                        breakout_triggered = True
                if breakout_triggered:
                    state.last_breakout_ts = now_ts
    except Exception:
        pass

    # 移除“极端偏向翻转”逻辑：完全尊重AI原始信号

    try:
        if signal_data.get('signal') == 'HOLD':
            tp_val = _parse_price_value(signal_data.get('take_profit'))
            sl_val = _parse_price_value(signal_data.get('stop_loss'))
            if tp_val is None:
                tp_val = _parse_price_value(signal_data.get('take_profit_price'))
            if sl_val is None:
                sl_val = _parse_price_value(signal_data.get('stop_loss_price'))
            if current_position and (tp_val is not None or sl_val is not None):
                pos_side = None
                try:
                    if state.ACCOUNT_POS_MODE == 'long_short':
                        pos_side = current_position.get('side')
                except Exception:
                    pos_side = None
                ok = set_position_tp_sl_for_okx(tp_val, sl_val, pos_side)
                if ok:
                    print(f"HOLD信号：已为现有持仓设置/更新TP/SL → TP={tp_val} SL={sl_val}")
                    try:
                        time.sleep(float(state.PRIVATE_UPDATE_INTERVAL or 1))
                        refreshed_position = get_current_position()
                        state.web_data['current_position'] = refreshed_position
                        balance = _with_rate_limit_retry(lambda: exchange.fetch_balance())
                        state.web_data['account_info'] = {
                            'usdt_balance': balance['USDT']['free'],
                            'total_equity': balance['USDT']['total']
                        }
                        state.web_data['last_update'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                        try:
                            side_check = 'sell' if (current_position and current_position.get('side') == 'long') else 'buy'
                            verify = verify_tp_sl_against_pending(tp_val, sl_val, side_check)
                            print(f"挂单核对结果: TP一致={verify.get('tp_match')} SL一致={verify.get('sl_match')}")
                            dedup = deduplicate_pending_tpsl(side_check)
                            if dedup.get('cancelled'):
                                print(f"发现并撤销重复策略委托: {dedup.get('cancelled')}")
                        except Exception:
                            pass
                        # 基于AI原始HOLD信号，记录到交易记录（供Web界面展示）
                        try:
                            from .okx import get_contract_size_btc as _ctbtc
                            ct_size_btc = float(_ctbtc())
                        except Exception:
                            ct_size_btc = 0.001
                        try:
                            pos_for_amount = refreshed_position or current_position
                            amount_btc = float(pos_for_amount['size']) * ct_size_btc if (pos_for_amount and pos_for_amount.get('size')) else 0.0
                        except Exception:
                            amount_btc = 0.0
                        try:
                            _sym = TRADE_CONFIG.get('symbol', 'BTC/USDT:USDT')
                            _base = _sym.split('/')[0] if '/' in _sym else 'BTC'
                        except Exception:
                            _sym = 'BTC/USDT:USDT'
                            _base = 'BTC'
                        try:
                            trade_record = {
                                'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                                'symbol': _sym,
                                'signal': 'HOLD',
                                'price': price_data['price'],
                                'amount': amount_btc,
                                'base': _base,
                                'confidence': (signal_data.get('confidence') or '').upper(),
                                'reason': signal_data.get('reason')
                            }
                            state.web_data['trade_history'].append(trade_record)
                            if len(state.web_data['trade_history']) > 100:
                                state.web_data['trade_history'].pop(0)
                            try:
                                append_trade_to_file(trade_record)
                            except Exception:
                                pass
                        except Exception:
                            pass
                    except Exception:
                        pass
                else:
                    print(f"HOLD信号：设置持仓TP/SL失败 → TP={tp_val} SL={sl_val}")
                return
    except Exception:
        pass

    min_trade_interval = int(config.TRADE_MIN_INTERVAL_SECONDS)
    if not breakout_triggered and state.last_trade_time is not None:
        time_since_last_trade = (datetime.now() - state.last_trade_time).total_seconds()
        if time_since_last_trade < min_trade_interval:
            remaining_time = min_trade_interval - time_since_last_trade
            print(f"🔒 距上次交易仅 {time_since_last_trade:.0f} 秒，需等待 {remaining_time:.0f} 秒后才能交易")
            return

    if not breakout_triggered and current_position and signal_data['signal'] != 'HOLD':
        current_side = current_position['side']
        if signal_data['signal'] == 'BUY':
            new_side = 'long'
        elif signal_data['signal'] == 'SELL':
            new_side = 'short'
        else:
            new_side = None
        if new_side != current_side:
            if signal_data['confidence'] != 'HIGH':
                print(f"🔒 非高信心反转信号，保持现有{current_side}仓")
                return

    print(f"交易信号: {signal_data['signal']}")
    print(f"信心程度: {signal_data['confidence']}")
    print(f"理由: {signal_data['reason']}")
    # 安全格式化止损/止盈（支持 None 与备用字段 *_price）
    sl_val = _parse_price_value(signal_data.get('stop_loss'))
    if sl_val is None:
        sl_val = _parse_price_value(signal_data.get('stop_loss_price'))
    tp_val = _parse_price_value(signal_data.get('take_profit'))
    if tp_val is None:
        tp_val = _parse_price_value(signal_data.get('take_profit_price'))
    sl_str = f"${sl_val:,.2f}" if sl_val is not None else "未设置"
    tp_str = f"${tp_val:,.2f}" if tp_val is not None else "未设置"
    print(f"止损: {sl_str}")
    print(f"止盈: {tp_str}")
    print(f"当前持仓: {current_position}")
    try:
        set_symbol_tpsl_expected(TRADE_CONFIG.get('symbol'), tp_val, sl_val)
    except Exception:
        pass

    if signal_data['confidence'] == 'LOW' and not TRADE_CONFIG['test_mode']:
        print("⚠️ 低信心信号，跳过执行")
        return

    if TRADE_CONFIG['test_mode']:
        print("测试模式 - 仅模拟交易")
        return

    try:
        executed_contracts = 0
        usdt_balance = None
        try:
            cached_balance = state.web_data.get('account_info', {})
            usdt_balance = cached_balance.get('usdt_balance', None)
        except Exception:
            usdt_balance = None
        if usdt_balance is None:
            balance = _with_rate_limit_retry(lambda: exchange.fetch_balance())
            usdt_balance = balance['USDT']['free']

        base_amount_btc = TRADE_CONFIG['amount']
        desired_contracts = btc_amount_to_okx_contracts(base_amount_btc)
        # 若AI提供了显式合约张数（size/sz），优先采用
        try:
            raw_size = signal_data.get('size')
            if raw_size is None:
                raw_size = signal_data.get('sz')
            if raw_size is not None:
                szv = int(float(raw_size))
                if szv > 0:
                    print(f"AI指定下单张数: {szv} (覆盖默认 {desired_contracts})")
                    desired_contracts = szv
        except Exception:
            pass
        mark_price = price_data['price']
        # 使用每币种杠杆进行保证金估算
        try:
            lev = int(get_symbol_leverage(TRADE_CONFIG.get('symbol')))
        except Exception:
            lev = int(TRADE_CONFIG.get('leverage', 10) or 10)
        required_margin = estimate_required_margin_usdt(desired_contracts, mark_price, lev)

        if usdt_balance is None:
            print("⚠️ 可用余额未知，跳过交易以保证安全")
            return

        if required_margin > usdt_balance * 0.8:
            max_contracts = int((usdt_balance * 0.8) / max(estimate_required_margin_usdt(1, mark_price, lev), 1e-9))
            if max_contracts < 1:
                print(f"⚠️ 保证金不足，跳过交易。需要: {required_margin:.2f} USDT, 可用: {usdt_balance:.2f} USDT")
                return
            print(f"⚠️ 保证金不足，自动将下单张数从 {desired_contracts} 调整为 {max_contracts}")
            desired_contracts = max_contracts

        # 开仓单不再附带TP/SL，避免OKX对 ordType 的参数冲突（51000）。
        # 若AI提供TP/SL，将在下单后通过策略委托单独设置。
        try:
            tp_input = signal_data.get('take_profit')
            sl_input = signal_data.get('stop_loss')
            if tp_input is None and sl_input is None:
                print("未设置TP/SL（AI显式返回None，按规则不设置）")
            else:
                print("TP/SL 将在下单后以策略委托形式单独设置")
        except Exception:
            pass

        if signal_data['signal'] == 'BUY':
            if current_position and current_position['side'] == 'short':
                print("平空仓并开多仓...")
                params = {'tdMode': 'cross', 'reduceOnly': True, 'tag': '60bb4a8d3416BCDE'}
                if state.ACCOUNT_POS_MODE == 'long_short':
                    params['posSide'] = 'short'
                try:
                    entry = float(current_position.get('entry_price') or 0)
                    size_contracts = float(current_position.get('size') or 0)
                    from .okx import get_contract_size_btc
                    ct_size_btc = get_contract_size_btc()
                    close_price = mark_price
                    realized = (entry - close_price) * size_contracts * ct_size_btc
                except Exception:
                    realized = 0.0
                close_px = compute_aggressive_limit_price('buy', mark_price)
                _with_rate_limit_retry(lambda: exchange.create_order(symbol=TRADE_CONFIG['symbol'], type='limit', side='buy', amount=current_position['size'], price=close_px, params=params))
                try:
                    update_win_statistics(realized, size_contracts)
                except Exception:
                    pass
                time.sleep(1)
                long_size = desired_contracts
                params = {'tdMode': 'cross', 'tag': '60bb4a8d3416BCDE'}
                if state.ACCOUNT_POS_MODE == 'long_short':
                    params['posSide'] = 'long'
                p2 = params
                open_px = compute_aggressive_limit_price('buy', mark_price)
                _with_rate_limit_retry(lambda: exchange.create_order(symbol=TRADE_CONFIG['symbol'], type='limit', side='buy', amount=long_size, price=open_px, params=p2))
                state.realized_profit_usdt += realized
                save_realized_pnl()
                try:
                    executed_contracts += int(long_size)
                except Exception:
                    pass
            elif current_position and current_position['side'] == 'long':
                try:
                    if int(getattr(config, 'PYRAMID_ENABLED', 1)) == 1:
                        now_ts = time.time()
                        if now_ts - state.last_pyramid_ts >= int(getattr(config, 'PYRAMID_MIN_INTERVAL_SEC', 60)) and state.pyramid_adds_long < int(getattr(config, 'PYRAMID_MAX_ADDS', 3)):
                            required = int(getattr(config, 'PYRAMID_CONSEC_SIGNALS_FOR_ADD', 2))
                            recent = [s['signal'] for s in state.signal_history[-(required-1):]] if required > 1 else []
                            if all(sig == 'BUY' for sig in recent):
                                add_ratio = float(getattr(config, 'PYRAMID_ADD_RATIO', 0.5))
                                add_amount_btc = max(0.0, base_amount_btc * add_ratio)
                                if add_amount_btc > 0:
                                    print(f"➕ 连涨加仓BUY，比例{add_ratio:.2f}，下单BTC={add_amount_btc}")
                                    add_contracts = btc_amount_to_okx_contracts(add_amount_btc)
                                    params = {'tdMode': 'cross', 'tag': '60bb4a8d3416BCDE'}
                                    if state.ACCOUNT_POS_MODE == 'long_short':
                                        params['posSide'] = 'long'
                                    p3 = params
                                    add_px = compute_aggressive_limit_price('buy', mark_price)
                                    _with_rate_limit_retry(lambda: exchange.create_order(symbol=TRADE_CONFIG['symbol'], type='limit', side='buy', amount=add_contracts, price=add_px, params=p3))
                                    state.pyramid_adds_long += 1
                                    state.last_pyramid_ts = now_ts
                                    print(f"连涨加仓完成，累计加仓次数(long)={state.pyramid_adds_long}")
                                    try:
                                        executed_contracts += int(add_contracts)
                                    except Exception:
                                        pass
                                else:
                                    print("连涨加仓比例为0，跳过")
                            else:
                                print("未满足连涨加仓的连续BUY次数要求，跳过")
                        else:
                            print("连涨加仓冷却中或达到上限，跳过")
                except Exception:
                    pass
                print("已有多头持仓，保持现状")
            else:
                print("开多仓...")
                long_size = desired_contracts
                params = {'tdMode': 'cross', 'tag': '60bb4a8d3416BCDE'}
                if state.ACCOUNT_POS_MODE == 'long_short':
                    params['posSide'] = 'long'
                p1 = params
                open_px2 = compute_aggressive_limit_price('buy', mark_price)
                _with_rate_limit_retry(lambda: exchange.create_order(symbol=TRADE_CONFIG['symbol'], type='limit', side='buy', amount=long_size, price=open_px2, params=p1))
                try:
                    executed_contracts += int(long_size)
                except Exception:
                    pass

        elif signal_data['signal'] == 'SELL':
            if current_position and current_position['side'] == 'long':
                print("平多仓并开空仓...")
                params = {'tdMode': 'cross', 'reduceOnly': True, 'tag': '60bb4a8d3416BCDE'}
                if state.ACCOUNT_POS_MODE == 'long_short':
                    params['posSide'] = 'long'
                try:
                    entry = float(current_position.get('entry_price') or 0)
                    size_contracts = float(current_position.get('size') or 0)
                    from .okx import get_contract_size_btc
                    ct_size_btc = get_contract_size_btc()
                    close_price = mark_price
                    realized = (close_price - entry) * size_contracts * ct_size_btc
                except Exception:
                    realized = 0.0
                close_px = compute_aggressive_limit_price('sell', mark_price)
                _with_rate_limit_retry(lambda: exchange.create_order(symbol=TRADE_CONFIG['symbol'], type='limit', side='sell', amount=current_position['size'], price=close_px, params=params))
                try:
                    update_win_statistics(realized, size_contracts)
                except Exception:
                    pass
                time.sleep(1)
                short_size = desired_contracts
                params = {'tdMode': 'cross', 'tag': '60bb4a8d3416BCDE'}
                if state.ACCOUNT_POS_MODE == 'long_short':
                    params['posSide'] = 'short'
                p4 = params
                open_px = compute_aggressive_limit_price('sell', mark_price)
                _with_rate_limit_retry(lambda: exchange.create_order(symbol=TRADE_CONFIG['symbol'], type='limit', side='sell', amount=short_size, price=open_px, params=p4))
                state.realized_profit_usdt += realized
                save_realized_pnl()
                try:
                    executed_contracts += int(short_size)
                except Exception:
                    pass
            elif current_position and current_position['side'] == 'short':
                try:
                    if int(getattr(config, 'PYRAMID_ENABLED', 1)) == 1:
                        now_ts = time.time()
                        if now_ts - state.last_pyramid_ts >= int(getattr(config, 'PYRAMID_MIN_INTERVAL_SEC', 60)) and state.pyramid_adds_short < int(getattr(config, 'PYRAMID_MAX_ADDS', 3)):
                            required = int(getattr(config, 'PYRAMID_CONSEC_SIGNALS_FOR_ADD', 2))
                            recent = [s['signal'] for s in state.signal_history[-(required-1):]] if required > 1 else []
                            if all(sig == 'SELL' for sig in recent):
                                add_ratio = float(getattr(config, 'PYRAMID_ADD_RATIO', 0.5))
                                add_amount_btc = max(0.0, base_amount_btc * add_ratio)
                                if add_amount_btc > 0:
                                    print(f"➕ 连跌加仓SELL，比例{add_ratio:.2f}，下单BTC={add_amount_btc}")
                                    add_contracts = btc_amount_to_okx_contracts(add_amount_btc)
                                    params = {'tdMode': 'cross', 'tag': '60bb4a8d3416BCDE'}
                                    if state.ACCOUNT_POS_MODE == 'long_short':
                                        params['posSide'] = 'short'
                                    p5 = params
                                    add_px = compute_aggressive_limit_price('sell', mark_price)
                                    _with_rate_limit_retry(lambda: exchange.create_order(symbol=TRADE_CONFIG['symbol'], type='limit', side='sell', amount=add_contracts, price=add_px, params=p5))
                                    state.pyramid_adds_short += 1
                                    state.last_pyramid_ts = now_ts
                                    print(f"连跌加仓完成，累计加仓次数(short)={state.pyramid_adds_short}")
                                    try:
                                        executed_contracts += int(add_contracts)
                                    except Exception:
                                        pass
                                else:
                                    print("连跌加仓比例为0，跳过")
                            else:
                                print("未满足连跌加仓的连续SELL次数要求，跳过")
                        else:
                            print("连跌加仓冷却中或达到上限，跳过")
                except Exception:
                    pass
                print("已有空头持仓，保持现状")
            else:
                print("开空仓...")
                short_size = desired_contracts
                params = {'tdMode': 'cross', 'tag': '60bb4a8d3416BCDE'}
                if state.ACCOUNT_POS_MODE == 'long_short':
                    params['posSide'] = 'short'
                p6 = params
                open_px3 = compute_aggressive_limit_price('sell', mark_price)
                _with_rate_limit_retry(lambda: exchange.create_order(symbol=TRADE_CONFIG['symbol'], type='limit', side='sell', amount=short_size, price=open_px3, params=p6))
                try:
                    executed_contracts += int(short_size)
                except Exception:
                    pass

        print("订单执行成功")

        state.last_trade_time = datetime.now()
        time.sleep(float(state.PRIVATE_UPDATE_INTERVAL or 1))
        refreshed_position = None
        for _ in range(3):
            time.sleep(1)
            refreshed_position = get_current_position()
            if refreshed_position:
                break
        print(f"更新后持仓: {refreshed_position}")

        try:
            side_check = None
            if refreshed_position and refreshed_position.get('side'):
                side_check = 'sell' if refreshed_position['side'] == 'long' else 'buy'
            expected_tp = _parse_price_value(signal_data.get('take_profit'))
            expected_sl = _parse_price_value(signal_data.get('stop_loss'))
            # 若AI提供了TP/SL，下单后单独设置策略委托
            if expected_tp is not None or expected_sl is not None:
                try:
                    set_ok = set_position_tp_sl_for_okx(expected_tp, expected_sl, refreshed_position.get('side') if refreshed_position else None)
                    if set_ok:
                        print(f"已提交策略委托TP/SL设置: TP={expected_tp} SL={expected_sl}")
                        time.sleep(1)
                except Exception as _e:
                    print(f"设置策略委托TP/SL失败: {_e}")
            try:
                set_symbol_tpsl_expected(TRADE_CONFIG.get('symbol'), expected_tp, expected_sl)
            except Exception:
                pass
            verify = verify_tp_sl_against_pending(expected_tp, expected_sl, side_check)
            print(f"下单后挂单核对: TP一致={verify.get('tp_match')} SL一致={verify.get('sl_match')}")
            dedup = deduplicate_pending_tpsl(side_check)
            if dedup.get('cancelled'):
                print(f"发现并撤销重复策略委托: {dedup.get('cancelled')}")
        except Exception:
            pass

        # 计算记录用数量（BTC）：
        # - HOLD → 当前持仓BTC数量
        # - BUY/SELL → 实际下单BTC数量（考虑保证金调整/加仓）
        try:
            ct_size_btc = float(get_contract_size_btc())
        except Exception:
            ct_size_btc = 0.001
        amount_btc = 0.0
        try:
            if signal_data['signal'] == 'HOLD':
                if current_position and current_position.get('size'):
                    amount_btc = float(current_position['size']) * ct_size_btc
                else:
                    amount_btc = 0.0
            else:
                amount_btc = float(executed_contracts) * ct_size_btc
        except Exception:
            amount_btc = 0.0

        try:
            _sym = TRADE_CONFIG.get('symbol', 'BTC/USDT:USDT')
            _base = _sym.split('/')[0]
        except Exception:
            _sym = 'BTC/USDT:USDT'
            _base = 'BTC'

        trade_record = {
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'symbol': _sym,
            'signal': signal_data['signal'],
            'price': price_data['price'],
            'amount': amount_btc,
            'base': _base,
            'confidence': signal_data['confidence'],
            'reason': signal_data['reason']
        }
        state.web_data['trade_history'].append(trade_record)
        if len(state.web_data['trade_history']) > 100:
            state.web_data['trade_history'].pop(0)
        try:
            append_trade_to_file(trade_record)
        except Exception:
            pass

    except Exception as e:
        print(f"订单执行失败: {e}")
        import traceback
        traceback.print_exc()



# ============ 组合级批量下单（不含TP/SL） ============
def build_plain_orders_for_decision(symbol: str, decision: dict, price_data: dict, current_position: dict = None) -> list:
    """根据单个币种决策构建普通批量下单条目（不包含任何止盈止损策略委托）。

    返回列表元素形如：
    {
      'symbol': symbol,
      'side': 'buy'|'sell',
      'ordType': 'limit',
      'sz': int,
      'px': float,
      'posSide': 'long'|'short' (可选),
      'reduceOnly': True|False (可选),
      'tag': '...'
    }
    """
    try:
        if not isinstance(decision, dict):
            return []
        signal = (decision.get('signal') or '').upper()
        if signal == 'HOLD':
            return []

        # 目标张数：优先AI给的 size/sz，否则按默认 amount 换算
        base_amount_btc = TRADE_CONFIG['amount']
        desired_contracts = btc_amount_to_okx_contracts(base_amount_btc)
        try:
            raw_size = decision.get('size')
            if raw_size is None:
                raw_size = decision.get('sz')
            if raw_size is not None:
                szv = int(float(raw_size))
                if szv > 0:
                    desired_contracts = szv
        except Exception:
            pass

        mark_price = float(price_data.get('price')) if price_data and price_data.get('price') is not None else None
        if mark_price is None:
            return []

        orders = []
        new_side = 'long' if signal == 'BUY' else 'short'

        # 双向或净值模式下的 posSide 设置
        pos_side_open = None
        try:
            if ACCOUNT_POS_MODE == 'long_short':
                pos_side_open = new_side
        except Exception:
            pos_side_open = None

        # 如果有反向持仓，先构建平仓单（reduceOnly）
        try:
            if current_position and current_position.get('side') and current_position.get('size'):
                cur_side = current_position.get('side')
                if cur_side in ('long', 'short') and cur_side != new_side:
                    close_side = 'sell' if cur_side == 'long' else 'buy'
                    close_px = compute_aggressive_limit_price(close_side, mark_price)
                    close_item = {
                        'symbol': symbol,
                        'side': close_side,
                        'ordType': 'limit',
                        'sz': int(float(current_position.get('size'))),
                        'px': float(close_px),
                        'reduceOnly': True,
                    }
                    try:
                        if ACCOUNT_POS_MODE == 'long_short':
                            close_item['posSide'] = cur_side
                        else:
                            pass
                    except Exception:
                        pass
                    orders.append(close_item)
        except Exception:
            pass

        # 开仓单（不含TP/SL）
        open_side = 'buy' if signal == 'BUY' else 'sell'
        # 如果已有同向持仓，当前版本不进行批量加仓，保持现状
        if current_position and current_position.get('side') == new_side:
            return orders

        open_px = compute_aggressive_limit_price(open_side, mark_price)
        open_item = {
            'symbol': symbol,
            'side': open_side,
            'ordType': 'limit',
            'sz': int(desired_contracts),
            'px': float(open_px),
        }
        if pos_side_open:
            open_item['posSide'] = pos_side_open
        orders.append(open_item)

        return orders
    except Exception:
        return []


def execute_portfolio_trades_batch(decisions: list, symbol_to_price_data: dict):
    """组合级批量下单：
    - 收集所有币种的普通订单（不含TP/SL）
    - 通过 /api/v5/trade/batch-orders 批量提交
    - 随后逐币种按原逻辑设置TP/SL（策略委托），不纳入批量
    """
    try:
        if not isinstance(decisions, list) or not decisions:
            return

        # 获取当前全部持仓，构建 symbol -> position 映射
        pos_list = get_all_positions(TRADE_CONFIG.get('symbols'))
        pos_map = {}
        try:
            for p in pos_list or []:
                if isinstance(p, dict) and p.get('symbol'):
                    pos_map[p['symbol']] = p
        except Exception:
            pos_map = {}

        all_orders = []
        tpsl_plan = []  # (symbol, tp, sl)
        orig_open_sz_by_symbol = {}

        for dec in decisions:
            try:
                sym = dec.get('symbol')
                if sym not in symbol_to_price_data:
                    continue
                price_data = symbol_to_price_data.get(sym)
                curpos = pos_map.get(sym)

                # 构建普通订单（不含TP/SL）
                orders = build_plain_orders_for_decision(sym, dec, price_data, curpos)
                if orders:
                    all_orders.extend(orders)
                    # 记录原始开仓张数（用于后续判断是否因预算被缩减）
                    try:
                        for od in orders:
                            if str(od.get('reduceOnly')).lower() not in ('true', '1'):
                                orig_open_sz_by_symbol[sym] = int(float(od.get('sz')))
                    except Exception:
                        pass

                # 记录TP/SL计划（批量下单后再逐个设置）
                tp_val = dec.get('take_profit')
                if tp_val is None:
                    tp_val = dec.get('take_profit_price')
                sl_val = dec.get('stop_loss')
                if sl_val is None:
                    sl_val = dec.get('stop_loss_price')
                tpsl_plan.append((sym, tp_val, sl_val))
            except Exception:
                continue

        # 批量前保证金预算与动态缩减
        final_orders = []
        executed_open_plan = {}  # sym -> {'sz': int, 'px': float}
        if all_orders:
            # 账户可用余额
            usdt_free = None
            try:
                acct = get_account_overview() or {}
                usdt_free = float(acct.get('usdt_free') or 0.0)
            except Exception:
                usdt_free = None
            if usdt_free is None or usdt_free <= 0:
                try:
                    balance = _with_rate_limit_retry(lambda: exchange.fetch_balance())
                    usdt_free = float((balance.get('USDT') or {}).get('free') or 0.0)
                except Exception:
                    usdt_free = 0.0

            try:
                budget_ratio = float(getattr(config, 'BATCH_MARGIN_BUDGET_RATIO', 0.8))
                budget_usdt = max(0.0, usdt_free * budget_ratio)
            except Exception:
                budget_usdt = max(0.0, usdt_free * 0.8)

            # 平仓单优先（不计预算），开仓单按AI信心从高到低分配
            closers = []
            openers = []
            for od in all_orders:
                try:
                    if str(od.get('reduceOnly')).lower() in ('true', '1'):
                        closers.append(dict(od))
                    else:
                        openers.append(dict(od))
                except Exception:
                    openers.append(dict(od))

            conf_rank = {'HIGH': 3, 'MEDIUM': 2, 'LOW': 1}
            sym_to_conf = {}
            try:
                for d in decisions:
                    s = (d.get('symbol') or '').strip()
                    c = (d.get('confidence') or 'LOW').upper()
                    sym_to_conf[s] = conf_rank.get(c, 1)
            except Exception:
                sym_to_conf = {}
            openers.sort(key=lambda x: sym_to_conf.get(x.get('symbol'), 1), reverse=True)

            final_orders.extend(closers)
            remaining_budget = float(budget_usdt)
            for od in openers:
                try:
                    sym = od.get('symbol')
                    px = float(od.get('px')) if od.get('px') is not None else float(symbol_to_price_data.get(sym, {}).get('price') or 0)
                    if px <= 0:
                        continue
                    try:
                        sz = int(float(od.get('sz')))
                    except Exception:
                        continue
                    if sz <= 0:
                        continue
                    try:
                        lev = int(get_symbol_leverage(sym))
                    except Exception:
                        lev = int(TRADE_CONFIG.get('leverage', 10) or 10)
                    req_full = estimate_required_margin_usdt_for_symbol(sz, px, lev, sym)
                    if req_full <= remaining_budget:
                        final_orders.append(od)
                        remaining_budget -= req_full
                        executed_open_plan[sym] = {'sz': sz, 'px': px}
                        continue
                    req_per = max(estimate_required_margin_usdt_for_symbol(1, px, lev, sym), 1e-9)
                    max_sz = int(remaining_budget / req_per)
                    if max_sz >= 1:
                        new_od = dict(od)
                        new_od['sz'] = max_sz
                        final_orders.append(new_od)
                        remaining_budget -= (max_sz * req_per)
                        executed_open_plan[sym] = {'sz': max_sz, 'px': px}
                    # 否则跳过
                except Exception:
                    continue

        # 批量提交普通订单（分片每批最大20单）
        if final_orders:
            for i in range(0, len(final_orders), 20):
                chunk = final_orders[i:i+20]
                resp = place_batch_orders(chunk)
                code = str(resp.get('code')) if isinstance(resp, dict) else None
                if code != '0':
                    print(f"❌ 批量下单失败: {resp}")
                else:
                    data = resp.get('data') or []
                    print(f"✓ 批量下单成功: {len(data)}/{len(chunk)} 条")
                    try:
                        failures = [d for d in data if str(d.get('sCode')) != '0']
                        if failures:
                            print(f"⚠️ 部分订单失败: {failures}")
                    except Exception:
                        pass

        # 批量下单后，逐币种设置TP/SL（策略委托）——保持原有逻辑
        for sym, tp_raw, sl_raw in tpsl_plan:
            try:
                tp = _parse_price_value(tp_raw)
                sl = _parse_price_value(sl_raw)
                if tp is None and sl is None:
                    continue
                try:
                    switch_active_symbol(sym)
                    TRADE_CONFIG['symbol'] = sym
                except Exception:
                    TRADE_CONFIG['symbol'] = sym
                ok = set_position_tp_sl_for_okx(tp, sl, None)
                if ok:
                    print(f"{sym} 已设置/更新TP/SL → TP={tp} SL={sl}")
                    try:
                        set_symbol_tpsl_expected(sym, tp, sl)
                    except Exception:
                        pass
                    # 轻微等待后去重，避免重复策略委托残留
                    try:
                        time.sleep(0.5)
                        curpos2 = get_current_position()
                        side_check = 'sell' if (curpos2 and curpos2.get('side') == 'long') else 'buy'
                        dedup = deduplicate_pending_tpsl(side_check)
                        if dedup.get('cancelled'):
                            print(f"发现并撤销重复策略委托: {dedup.get('cancelled')}")
                    except Exception:
                        pass
                # 轻微等待，避免过快命中限频
                time.sleep(0.5)
            except Exception:
                continue

        # 记录交易历史与更新AI实施信息（基于最终开仓计划 executed_open_plan）
        try:
            # 构建决策映射，方便取用信号/理由
            dec_map = {}
            for d in decisions:
                try:
                    dsym = d.get('symbol')
                    if dsym:
                        dec_map[dsym] = d
                except Exception:
                    continue

            for sym, exec_info in executed_open_plan.items():
                try:
                    sz_exec = int(exec_info.get('sz') or 0)
                    if sz_exec <= 0:
                        continue
                    ct_size_btc = float(get_contract_size_btc_for_symbol(sym))
                    amount_btc = float(sz_exec) * ct_size_btc
                    # 价格用于展示沿用当前价（与单币种逻辑一致）
                    pinfo = symbol_to_price_data.get(sym) or {}
                    disp_px = pinfo.get('price')
                    base = 'BTC'
                    try:
                        base = sym.split('/')[0]
                    except Exception:
                        base = 'BTC'
                    dec = dec_map.get(sym) or {}
                    trade_record = {
                        'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                        'symbol': sym,
                        'signal': dec.get('signal') or 'HOLD',
                        'price': disp_px,
                        'amount': amount_btc,
                        'base': base,
                        'confidence': dec.get('confidence') or '-',
                        'reason': dec.get('reason') or ''
                    }
                    state.web_data['trade_history'].append(trade_record)
                    if len(state.web_data['trade_history']) > 100:
                        state.web_data['trade_history'].pop(0)
                    try:
                        append_trade_to_file(trade_record)
                    except Exception:
                        pass

                    # 更新内存中的最近AI决策项，标注实施结果
                    try:
                        ai_list = state.web_data.get('ai_decisions') or []
                        # 从末尾向前找到本符号的最新一条
                        for i in range(len(ai_list) - 1, -1, -1):
                            if (ai_list[i] or {}).get('symbol') == sym:
                                ai_list[i]['implemented_size'] = sz_exec
                                ai_list[i]['implemented_side'] = (dec.get('signal') or '').upper()
                                ai_list[i]['implemented_price'] = disp_px
                                want_sz = orig_open_sz_by_symbol.get(sym)
                                ai_list[i]['budget_adjusted'] = (want_sz is not None and int(want_sz) != int(sz_exec))
                                ai_list[i]['implemented_at'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                                break
                    except Exception:
                        pass
                except Exception:
                    continue
        except Exception:
            pass

        # 对于未产生开仓的币种（包括HOLD或预算受限未开仓），也基于AI原始回复记录一条交易记录
        try:
            decided_symbols = []
            try:
                for d in decisions:
                    s = d.get('symbol')
                    if s:
                        decided_symbols.append(s)
            except Exception:
                decided_symbols = []
            for sym in decided_symbols:
                if sym in (executed_open_plan.keys() if executed_open_plan else []):
                    continue
                try:
                    dec = dec_map.get(sym) or {}
                    ct_size_btc = float(get_contract_size_btc_for_symbol(sym))
                except Exception:
                    ct_size_btc = 0.001
                try:
                    pos = pos_map.get(sym)
                    amount_btc = float(pos.get('size') or 0) * ct_size_btc if pos else 0.0
                except Exception:
                    amount_btc = 0.0
                try:
                    pinfo = symbol_to_price_data.get(sym) or {}
                    disp_px = pinfo.get('price')
                    base = 'BTC'
                    try:
                        base = sym.split('/')[0]
                    except Exception:
                        base = 'BTC'
                    trade_record = {
                        'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                        'symbol': sym,
                        'signal': (dec.get('signal') or 'HOLD'),
                        'price': disp_px,
                        'amount': amount_btc,
                        'base': base,
                        'confidence': (dec.get('confidence') or '-'),
                        'reason': dec.get('reason') or ''
                    }
                    state.web_data['trade_history'].append(trade_record)
                    if len(state.web_data['trade_history']) > 100:
                        state.web_data['trade_history'].pop(0)
                    try:
                        append_trade_to_file(trade_record)
                    except Exception:
                        pass
                except Exception:
                    continue
        except Exception:
            pass

        # 更新最近交易时间（全局节流）
        try:
            state.last_trade_time = datetime.now()
        except Exception:
            pass
    except Exception as e:
        print(f"组合级批量下单流程失败: {e}")

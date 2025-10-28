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
)
from .context import exchange
from .okx import _with_rate_limit_retry
from .state import set_symbol_tpsl_expected
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



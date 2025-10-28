import time
from datetime import datetime
import config

from bot.context import AI_PROVIDER, AI_MODEL, exchange, TRADE_CONFIG, get_symbol_leverage, set_symbol_leverage
from bot import state
from bot.okx import get_current_position, setup_exchange, _with_rate_limit_retry
from bot.indicators import get_btc_ohlcv_enhanced
from bot.prompts import analyze_with_deepseek_with_retry, test_ai_connection
from bot.trade import execute_trade
from bot.utils import append_ai_decision_to_file, load_realized_pnl, AI_DECISIONS_LOG_PATH, append_profit_point_to_file


# 注入运行时节流配置到 state
state.PRIVATE_UPDATE_INTERVAL = float(config.PRIVATE_UPDATE_INTERVAL_SECONDS)
state.ANALYSIS_UPDATE_INTERVAL = float(config.ANALYSIS_UPDATE_INTERVAL_SECONDS)
state.SENTIMENT_TTL = float(config.SENTIMENT_CACHE_TTL_SECONDS)
state.AI_DECISION_INTERVAL = float(config.AI_DECISION_MIN_INTERVAL_SECONDS)


# 兼容旧接口与全局变量（供 web_server 等模块使用）
web_data = state.web_data
signal_history = state.signal_history
realized_profit_usdt = state.realized_profit_usdt
MIN_TRADE_INTERVAL = int(config.TRADE_MIN_INTERVAL_SECONDS)
AI_DECISIONS_LOG_PATH = AI_DECISIONS_LOG_PATH


def update_realtime_data():
    """实时更新价格和持仓数据（轻量级，不做AI决策）"""
    try:
        # 多币对轮询：只更新当前ACTIVE_SYMBOL的实时数据
        symbol = TRADE_CONFIG['symbol']
        ohlcv = _with_rate_limit_retry(lambda: exchange.fetch_ohlcv(symbol, TRADE_CONFIG['timeframe'], limit=1))
        if ohlcv and len(ohlcv) > 0:
            current_price = ohlcv[0][4]
            state.web_data['current_price'] = current_price

        now_ts = time.time()
        if now_ts - state.last_private_update_ts >= state.PRIVATE_UPDATE_INTERVAL:
            state.web_data['current_position'] = get_current_position()
            balance = _with_rate_limit_retry(lambda: exchange.fetch_balance())
            current_equity = balance['USDT']['total']
            if state.initial_balance is None:
                state.initial_balance = current_equity
            state.web_data['account_info'] = {
                'usdt_balance': balance['USDT']['free'],
                'total_equity': current_equity
            }
            if state.web_data['current_position']:
                state.web_data['performance']['total_profit'] = state.web_data['current_position'].get('unrealized_pnl', 0)
            state.last_private_update_ts = now_ts

            state.web_data['last_update'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    except Exception as e:
        print(f"⚠️ 实时数据更新失败: {e}")


def trading_bot():
    """主交易机器人函数 - 每周期执行一次决策"""
    print("\n" + "=" * 60)
    print(f"执行时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)
    # 支持多币对轮询：对 TRADE_CONFIG['symbols'] 依次切换执行
    symbols = TRADE_CONFIG.get('symbols', [TRADE_CONFIG['symbol']])
    for sym in symbols:
        try:
            # 切换全局活跃交易对（同步多符号隔离的运行态）
            from bot.state import switch_active_symbol as _switch
            _switch(sym)
            TRADE_CONFIG['symbol'] = sym
        except Exception:
            TRADE_CONFIG['symbol'] = sym

        price_data = get_btc_ohlcv_enhanced()
        if not price_data:
            continue

        try:
            _sym = price_data.get('symbol', sym)
        except Exception:
            _sym = sym
        print(f"{_sym} 当前价格: ${price_data['price']:,.2f}")
        print(f"数据周期: {TRADE_CONFIG['timeframe']}")
        print(f"价格变化: {price_data['price_change']:+.2f}%")

        signal_data = analyze_with_deepseek_with_retry(price_data)
        if signal_data.get('is_fallback', False):
            print("⚠️ 使用备用交易信号")

        try:
            account_info = state.web_data.get('account_info', {})
            current_equity = account_info.get('total_equity', None)
            if current_equity is None:
                # 节流：余额查询沿用 PRIVATE_UPDATE_INTERVAL_SECONDS 控制刷新
                from bot.state import ensure_symbol_bucket
                import time as _t
                b = ensure_symbol_bucket(sym)
                last_ts = float(b.get('last_private_update_ts') or 0)
                if (_t.time() - last_ts) >= float(config.PRIVATE_UPDATE_INTERVAL_SECONDS):
                    balance = _with_rate_limit_retry(lambda: exchange.fetch_balance())
                    current_equity = balance['USDT']['total']
                    state.web_data['account_info'] = {
                        'usdt_balance': balance['USDT']['free'],
                        'total_equity': current_equity
                    }
                    b['last_private_update_ts'] = _t.time()
                else:
                    current_equity = account_info.get('total_equity', 0)
            if state.initial_balance is None and current_equity is not None:
                state.initial_balance = current_equity
            current_position = get_current_position()
            unrealized_pnl = current_position.get('unrealized_pnl', 0) if current_position else 0
            total_profit = state.realized_profit_usdt + unrealized_pnl
            profit_rate = (total_profit / state.initial_balance * 100) if state.initial_balance and state.initial_balance > 0 else 0
            profit_point = {
                'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'equity': current_equity,
                'profit': total_profit,
                'profit_rate': profit_rate,
                'unrealized_pnl': unrealized_pnl
            }
            state.web_data['profit_curve'].append(profit_point)
            try:
                append_profit_point_to_file(profit_point)
            except Exception:
                pass
            if len(state.web_data['profit_curve']) > 200:
                state.web_data['profit_curve'].pop(0)
        except Exception as e:
            print(f"更新余额失败: {e}")
        
        state.web_data['current_price'] = price_data['price']
        state.web_data['current_position'] = get_current_position()
        state.web_data['last_update'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        state.web_data['kline_data'] = price_data['kline_data']

        # 合并AI决策并附带杠杆（如有）
        ai_decision = {
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'symbol': _sym,
            'signal': signal_data['signal'],
            'confidence': signal_data['confidence'],
            'reason': signal_data['reason'],
            'stop_loss': signal_data.get('stop_loss', None),
            'take_profit': signal_data.get('take_profit', None),
            'price': price_data['price'],
            'position_size': signal_data.get('position_size'),
            'trade_type': signal_data.get('trade_type'),
            'take_profit_price': signal_data.get('take_profit_price', signal_data.get('take_profit')),
            'stop_loss_price': signal_data.get('stop_loss_price', signal_data.get('stop_loss')),
            'trailing_stop_loss': signal_data.get('trailing_stop_loss'),
            'leverage': signal_data.get('leverage')
        }
        state.web_data['ai_decisions'].append(ai_decision)
        append_ai_decision_to_file(ai_decision)
    
        try:
            current_position = state.web_data.get('current_position')
            unrealized_pnl = current_position.get('unrealized_pnl', 0) if current_position else 0
            state.web_data['performance']['total_profit'] = state.realized_profit_usdt + unrealized_pnl
        except Exception:
            pass

        # 若AI给出杠杆，应用到运行时映射并尝试设置交易所杠杆
        try:
            ai_lev = signal_data.get('leverage')
            if ai_lev is not None:
                lv = int(float(ai_lev))
                lv = max(1, min(lv, 50))
                set_symbol_leverage(_sym, lv)
                try:
                    # 设置交易所杠杆（跨保证金模式）
                    _with_rate_limit_retry(lambda: exchange.set_leverage(lv, _sym, {'mgnMode': 'cross'}))
                    print(f"应用AI杠杆: {_sym} → {lv}x")
                except Exception as _e:
                    print(f"应用AI杠杆失败({_sym}): {_e}")
        except Exception:
            pass

        execute_trade(signal_data, price_data)


def main():
    print(f"{TRADE_CONFIG['symbol']} OKX自动交易机器人启动成功！")
    print(f"AI模型: {AI_PROVIDER.upper()} ({AI_MODEL})")
    print("融合技术指标策略 + OKX实盘接口")

    if TRADE_CONFIG['test_mode']:
        print("当前为模拟模式，不会真实下单")
    else:
        print("实盘交易模式，请谨慎操作！")

    print(f"交易周期: {TRADE_CONFIG['timeframe']}")
    print("已启用完整技术指标分析和持仓跟踪功能")

    load_realized_pnl()

    if not setup_exchange():
        print("交易所初始化失败，程序退出")
        return

    print(f"⏰ 执行频率: 每{config.BACKEND_DECISION_INTERVAL_SECONDS}秒进行AI决策分析")
    print(f"📊 数据更新: 每{config.BACKEND_REALTIME_UPDATE_INTERVAL_SECONDS}秒更新一次")
    print("🛡️  安全机制: 有防频繁交易保护，不是每次都交易")

    while True:
        try:
            trading_bot()
        except Exception as e:
            print(f"❌ 交易执行出错: {e}")
            import traceback
            traceback.print_exc()
        time.sleep(float(config.BACKEND_DECISION_INTERVAL_SECONDS))


if __name__ == "__main__":
    main()



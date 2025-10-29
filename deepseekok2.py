import time
from datetime import datetime
import config

from bot.context import AI_PROVIDER, AI_MODEL, exchange, TRADE_CONFIG, get_symbol_leverage, set_symbol_leverage
from bot import state
from bot.okx import get_current_position, setup_exchange, _with_rate_limit_retry
from bot.indicators import get_btc_ohlcv_enhanced
from bot.prompts import analyze_with_deepseek_with_retry, test_ai_connection, analyze_portfolio_with_deepseek
from bot.trade import execute_trade, execute_portfolio_trades_batch
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
    """主交易机器人函数 - 每周期执行一次（组合级）AI问答与多币种执行"""
    print("\n" + "=" * 60)
    print(f"执行时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    symbols = TRADE_CONFIG.get('symbols', [TRADE_CONFIG['symbol']])

    # 先轮询收集每个币种的数据（不调用AI）
    sym_to_pd = {}
    for sym in symbols:
        try:
            from bot.state import switch_active_symbol as _switch
            _switch(sym)
            TRADE_CONFIG['symbol'] = sym
        except Exception:
            TRADE_CONFIG['symbol'] = sym
        pd_obj = get_btc_ohlcv_enhanced()
        if pd_obj:
            sym_to_pd[sym] = pd_obj

    if not sym_to_pd:
        return

    # 组合级一次问答，返回多币种决策
    decisions = analyze_portfolio_with_deepseek(sym_to_pd)
    if not decisions:
        print("⚠️ 未得到组合级决策，跳过本轮")
        return

    # 记录本轮组合级信号/信心统计，供前端展示
    try:
        sig_stats = {'BUY': 0, 'SELL': 0, 'HOLD': 0}
        conf_stats = {'HIGH': 0, 'MEDIUM': 0, 'LOW': 0}
        for d in decisions:
            try:
                s = (d.get('signal') or 'HOLD').upper()
                c = (d.get('confidence') or 'LOW').upper()
                if s not in sig_stats:
                    s = 'HOLD'
                if c not in conf_stats:
                    c = 'LOW'
                sig_stats[s] += 1
                conf_stats[c] += 1
            except Exception:
                continue
        state.web_data['last_portfolio_stats'] = {
            'signal_stats': sig_stats,
            'confidence_stats': conf_stats,
            'total_decisions': len(decisions),
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }
    except Exception:
        pass

    # 更新一次账户曲线（以组合视角，按当前活跃symbol）
    try:
        account_info = state.web_data.get('account_info', {})
        current_equity = account_info.get('total_equity', None)
        if current_equity is None:
            balance = _with_rate_limit_retry(lambda: exchange.fetch_balance())
            current_equity = balance['USDT']['total']
            state.web_data['account_info'] = {
                'usdt_balance': balance['USDT']['free'],
                'total_equity': current_equity
            }
        if state.initial_balance is None and current_equity is not None:
            state.initial_balance = current_equity
        curpos = get_current_position()
        unrealized_pnl = curpos.get('unrealized_pnl', 0) if curpos else 0
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

    # 依次应用每个币种的决策（记录与准备），执行阶段改为组合级批量提交
    for dec in decisions:
        sym = dec.get('symbol')
        if sym not in sym_to_pd:
            continue

        # 切换上下文与快照
        try:
            from bot.state import switch_active_symbol as _switch
            _switch(sym)
            TRADE_CONFIG['symbol'] = sym
        except Exception:
            TRADE_CONFIG['symbol'] = sym

        price_data = sym_to_pd[sym]
        print(f"{sym} 当前价格: ${price_data['price']:,.2f}")
        print(f"数据周期: {TRADE_CONFIG['timeframe']}")
        print(f"价格变化: {price_data['price_change']:+.2f}%")

        # 维护每币种的 signal_history 内存
        try:
            state.signal_history.append({
                'signal': (dec.get('signal') or '').upper(),
                'confidence': (dec.get('confidence') or '').upper(),
                'reason': dec.get('reason'),
                'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            })
            if len(state.signal_history) > 30:
                state.signal_history.pop(0)
        except Exception:
            pass

        # 记录AI决策
        ai_decision = {
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'symbol': sym,
            'signal': dec.get('signal'),
            'confidence': dec.get('confidence'),
            'reason': dec.get('reason'),
            'stop_loss': dec.get('stop_loss'),
            'take_profit': dec.get('take_profit'),
            'price': price_data['price'],
            'take_profit_price': dec.get('take_profit_price', dec.get('take_profit')),
            'stop_loss_price': dec.get('stop_loss_price', dec.get('stop_loss')),
            'trailing_stop_loss': dec.get('trailing_stop_loss'),
            'leverage': dec.get('leverage')
        }
        state.web_data['ai_decisions'].append(ai_decision)
        append_ai_decision_to_file(ai_decision)

        # 应用杠杆建议
        try:
            ai_lev = dec.get('leverage')
            if ai_lev is not None:
                lv = int(float(ai_lev))
                lv = max(1, min(lv, 50))
                set_symbol_leverage(sym, lv)
                try:
                    _with_rate_limit_retry(lambda: exchange.set_leverage(lv, sym, {'mgnMode': 'cross'}))
                    print(f"应用AI杠杆: {sym} → {lv}x")
                except Exception as _e:
                    print(f"应用AI杠杆失败({sym}): {_e}")
        except Exception:
            pass

        # 此处不再逐个下单，改为在循环结束后统一批量下单
        pass

    # 统一批量提交普通订单（不含TP/SL），随后逐币种设置TP/SL
    try:
        execute_portfolio_trades_batch(decisions, sym_to_pd)
    except Exception as e:
        print(f"批量下单阶段出错: {e}")


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



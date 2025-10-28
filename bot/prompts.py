import os
import time
import pandas as pd
from datetime import datetime

from .context import AI_PROVIDER, AI_MODEL, ai_client, TRADE_CONFIG, get_symbol_leverage
from . import state
from .sentiment import get_sentiment_indicators
from .okx import (
    get_current_position,
    get_open_orders_pending,
    get_algo_orders_pending,
    format_open_orders_for_prompt,
    format_algo_orders_for_prompt,
    _parse_price_value,
)
from .utils import safe_json_parse, extract_error_info


def _format_prev_ai_raw_for_prompt(max_chars: int = 800) -> str:
    try:
        if not state.ai_raw_history:
            return "无"
        previews = state.ai_raw_history[-2:]
        lines = []
        for idx, raw in enumerate(previews, start=1):
            s = str(raw or '')
            if len(s) > max_chars:
                s = s[:max_chars] + "..."
            lines.append(f"{idx}) {s}")
        return "\n".join(lines)
    except Exception:
        return "无"


def test_ai_connection():
    try:
        print(f"🔍 测试 {AI_PROVIDER.upper()} 连接...")
        response = ai_client.chat.completions.create(
            model=AI_MODEL,
            messages=[{"role": "user", "content": "Hello"}],
            max_tokens=10,
            timeout=10.0
        )
        if response and response.choices:
            state.web_data['ai_model_info']['status'] = 'connected'
            state.web_data['ai_model_info']['last_check'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            state.web_data['ai_model_info']['error_message'] = None
            print(f"✓ {AI_PROVIDER.upper()} 连接正常")
            return True
        else:
            state.web_data['ai_model_info']['status'] = 'error'
            state.web_data['ai_model_info']['last_check'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            state.web_data['ai_model_info']['error_message'] = '响应为空'
            print(f"❌ {AI_PROVIDER.upper()} 连接失败: 响应为空")
            return False
    except Exception as e:
        err_type, err_code = extract_error_info(e)
        state.web_data['ai_model_info']['status'] = 'error'
        state.web_data['ai_model_info']['last_check'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        state.web_data['ai_model_info']['error_message'] = str(e)
        state.web_data['ai_model_info']['error_type'] = err_type
        state.web_data['ai_model_info']['error_code'] = err_code
        code_text = err_code if err_code is not None else 'N/A'
        print(f"❌ {AI_PROVIDER.upper()} 连接失败: [{err_type} {code_text}] {e}")
        return False


def generate_technical_analysis_text(price_data):
    if 'technical_data' not in price_data:
        return "技术指标数据不可用"
    tech = price_data['technical_data']
    trend = price_data.get('trend_analysis', {})
    levels = price_data.get('levels_analysis', {})
    def safe_float(value, default=0):
        return float(value) if value and pd.notna(value) else default
    analysis_text = f"""
    【技术指标分析】
    📈 移动平均线:
    - 5周期: {safe_float(tech['sma_5']):.2f} | 价格相对: {(price_data['price'] - safe_float(tech['sma_5'])) / safe_float(tech['sma_5']) * 100:+.2f}%
    - 20周期: {safe_float(tech['sma_20']):.2f} | 价格相对: {(price_data['price'] - safe_float(tech['sma_20'])) / safe_float(tech['sma_20']) * 100:+.2f}%
    - 50周期: {safe_float(tech['sma_50']):.2f} | 价格相对: {(price_data['price'] - safe_float(tech['sma_50'])) / safe_float(tech['sma_50']) * 100:+.2f}%

    🎯 趋势分析:
    - 短期趋势: {trend.get('short_term', 'N/A')}
    - 中期趋势: {trend.get('medium_term', 'N/A')}
    - 整体趋势: {trend.get('overall', 'N/A')}
    - MACD方向: {trend.get('macd', 'N/A')}

    📊 动量指标:
    - RSI: {safe_float(tech['rsi']):.2f} ({'超买' if safe_float(tech['rsi']) > 70 else '超卖' if safe_float(tech['rsi']) < 30 else '中性'})
    - MACD: {safe_float(tech['macd']):.4f}
    - 信号线: {safe_float(tech['macd_signal']):.4f}

    🎚️ 布林带位置: {safe_float(tech['bb_position']):.2%} ({'上部' if safe_float(tech['bb_position']) > 0.7 else '下部' if safe_float(tech['bb_position']) < 0.3 else '中部'})

    💰 关键水平:
    - 静态阻力: {safe_float(levels.get('static_resistance', 0)):.2f}
    - 静态支撑: {safe_float(levels.get('static_support', 0)):.2f}
    """
    return analysis_text


def create_fallback_signal(price_data):
    return {
        "signal": "HOLD",
        "reason": "因技术分析暂时不可用，采取保守策略",
        "stop_loss": price_data['price'] * 0.98,
        "take_profit": price_data['price'] * 1.02,
        "confidence": "LOW",
        "is_fallback": True
    }


def analyze_with_deepseek(price_data):
    technical_analysis = generate_technical_analysis_text(price_data)
    kline_text = f"【最近5根{TRADE_CONFIG['timeframe']}K线数据】\n"
    for i, kline in enumerate(price_data['kline_data'][-5:]):
        trend = "阳线" if kline['close'] > kline['open'] else "阴线"
        change = ((kline['close'] - kline['open']) / kline['open']) * 100
        kline_text += f"K线{i + 1}: {trend} 开盘:{kline['open']:.2f} 收盘:{kline['close']:.2f} 涨跌:{change:+.2f}%\n"

    signal_text = ""
    if state.signal_history:
        last_signal = state.signal_history[-1]
        signal_text = f"\n【上次交易信号】\n信号: {last_signal.get('signal', 'N/A')}\n信心: {last_signal.get('confidence', 'N/A')}"

    sentiment_data = get_sentiment_indicators()
    if sentiment_data:
        sign = '+' if sentiment_data['net_sentiment'] >= 0 else ''
        sentiment_text = f"【市场情绪】乐观{sentiment_data['positive_ratio']:.1%} 悲观{sentiment_data['negative_ratio']:.1%} 净值{sign}{sentiment_data['net_sentiment']:.3f}"
    else:
        sentiment_text = "【市场情绪】数据暂不可用"
    print(sentiment_text)

    current_pos = get_current_position()
    position_text = "无持仓" if not current_pos else f"{current_pos['side']}仓, 数量: {current_pos['size']}, 盈亏: {current_pos['unrealized_pnl']:.2f}USDT"
    pnl_text = f", 持仓盈亏: {current_pos['unrealized_pnl']:.2f} USDT" if current_pos else ""

    try:
        open_orders = get_open_orders_pending(limit=20)
        open_orders_text = format_open_orders_for_prompt(open_orders, max_items=10)
    except Exception:
        open_orders_text = "无"
    try:
        algo_orders = get_algo_orders_pending(limit=20)
        algo_orders_text = format_algo_orders_for_prompt(algo_orders, max_items=10)
    except Exception:
        algo_orders_text = "无"

    mtf_text = ""
    try:
        if price_data.get('trend_4h') and price_data.get('boll_4h'):
            t4 = price_data['trend_4h']
            b4 = price_data['boll_4h']
            mtf_text += (
                f"【4H BOLL趋势】\n"
                f"- 趋势: {t4.get('overall','N/A')}  位置: {t4.get('bb_position',0):.2%}\n"
                f"- 上轨: {b4.get('bb_upper',0):.2f} 中轨: {b4.get('bb_middle',0):.2f} 下轨: {b4.get('bb_lower',0):.2f}\n"
            )
        if price_data.get('levels_15m'):
            lv15 = price_data['levels_15m']
            mtf_text += (
                f"【15m关键位】\n"
                f"- 阻力: {lv15.get('static_resistance',0):.2f} 支撑: {lv15.get('static_support',0):.2f}"
                f"  (BOLL上: {lv15.get('bb_upper',0):.2f} 中: {lv15.get('bb_middle',0):.2f} 下: {lv15.get('bb_lower',0):.2f})\n"
            )
        # 新增：斐波纳契与枢轴位简述
        if price_data.get('levels_analysis'):
            la = price_data['levels_analysis']
            fib = la.get('fibonacci') or {}
            piv = la.get('pivots') or {}
            fib_text = ''
            if fib:
                fib_text = (f"23.6%:{fib.get('fib_23_6',0):.2f} 38.2%:{fib.get('fib_38_2',0):.2f} "
                            f"50%:{fib.get('fib_50',0):.2f} 61.8%:{fib.get('fib_61_8',0):.2f} 78.6%:{fib.get('fib_78_6',0):.2f}")
            piv_text = ''
            if piv:
                piv_text = (f"PP:{piv.get('pp',0):.2f} R1:{piv.get('r1',0):.2f} S1:{piv.get('s1',0):.2f} "
                            f"R2:{piv.get('r2',0):.2f} S2:{piv.get('s2',0):.2f}")
            if fib_text:
                mtf_text += f"【斐波纳契回撤】{fib_text}\n"
            if piv_text:
                mtf_text += f"【枢轴位】{piv_text}\n"
    except Exception:
        pass

    prev_ai_raw = _format_prev_ai_raw_for_prompt()
    try:
        symbol = TRADE_CONFIG.get('symbol', 'BTC/USDT:USDT')
        base = symbol.split('/')[0]
    except Exception:
        symbol = 'BTC/USDT:USDT'
        base = 'BTC'

    # 每币种杠杆基线（结合当前配置与历史经验）
    try:
        baseline_lev = int(get_symbol_leverage(symbol))
    except Exception:
        baseline_lev = int(TRADE_CONFIG.get('leverage', 10) or 10)

    prompt = f"""
    你是一个专业的加密货币交易分析师。请基于以下{symbol} {TRADE_CONFIG['timeframe']} 周期数据进行分析：

    {kline_text}

    {technical_analysis}

    {signal_text}

    {sentiment_text}  # 情绪分析（如有）

    {mtf_text}  # 多周期补充（如有）

    【当前行情】
    - 当前价格: ${price_data['price']:,.2f}
    - 时间: {price_data['timestamp']}
    - 本K线最高: ${price_data['high']:,.2f}
    - 本K线最低: ${price_data['low']:,.2f}
    - 本K线成交量: {price_data['volume']:.2f} {base}
    - 价格变化: {price_data['price_change']:+.2f}%
    - 当前持仓: {position_text}{pnl_text}
    - 当前未成交普通订单（最多10条）：
    {open_orders_text}
    - 当前未成交策略订单（最多10条）：
    {algo_orders_text}
    
    
    【当前技术状况总览】
    - 整体趋势 (4H): {price_data['trend_analysis'].get('overall', 'N/A')}
    - 短期趋势 (15m): {price_data['trend_analysis'].get('short_term', 'N/A')}
    - RSI (15m): {price_data['technical_data'].get('rsi', 0):.1f} ({'超买' if price_data['technical_data'].get('rsi', 0) > 70 else '超卖' if price_data['technical_data'].get('rsi', 0) < 30 else '中性'})
    - MACD 方向 (15m): {price_data['trend_analysis'].get('macd', 'N/A')}

    【前两次AI原始回复（供一致性参考）】
    {prev_ai_raw}

    【分析要求】
    请依据以上情况，根据当前持仓情况给出明确交易指令，可以考虑分仓操作，对于浮亏的仓位，可以考虑加仓操作，对于盈利的仓位，可以考虑减仓操作。
    但是要注意鸡蛋不要放在一个篮子里，不要把所有仓位都放在一个方向上，要考虑风险分散。
    同时“贪多嚼不烂”，不要同时操作多个仓位，别人恐惧我贪婪，别人贪婪我恐惧。

    【风险与杠杆提示】
    - 基线杠杆建议: {baseline_lev}x（可根据波动率、趋势强度、信心度在合理范围内微调）
    - 若波动扩大、流动性变差或信心降低，适当降低杠杆；反之可小幅提高
    - 不可过度频繁变更杠杆，优先保持稳定的风险敞口

    【执行规则】
    - 若需要为“已有持仓”设置止盈/止损：如已存在同方向TP或SL，须先撤销旧单再设置新的TP/SL。
    - 如给出止盈/止损，请明确具体价格（美元），不要给相对百分比或范围。
    - 价格触发类型按 last 价格。
    - 对于BTC和ETH，可适当提高杠杆，对于其他币种，可适当降低杠杆。
    - 尽量设置止盈止损的时候，采用布林带上下轨作为参考、关键位、斐波纳契回撤位、支撑阻力位、移动平均线、相对价格等作为参考。

    请用以下 JSON 格式回复：
    {{
        "symbol": "{symbol}",
        "signal": "BUY|SELL|HOLD",
        "reason": "当前币种为{symbol}，说明为什么做出这个决定，建议做多还是做空，还是继续持仓，加仓还是减仓。",
        "stop_loss": 具体价格，（必填，如果当前未成交策略订单有合理的价格，则填充该价格）
        "take_profit": 具体价格，（必填，如果当前未成交策略订单有合理的价格，则填充该价格）
        "confidence": "HIGH|MEDIUM|LOW",
        "position_size": "REDUCED|NORMAL|AGGRESSIVE",
        "trade_type": "LONG|SHORT|HOLD|ADD|REDUCE",
        "take_profit_price": 具体价格,（可选，如果take_profit的与当前未成交策略订单当中相等，则填充None，否则填写新价格take_profit）
        "stop_loss_price": 具体价格,（可选，注意stop_loss不同，如果stop_loss的与当前未成交策略订单当中相等，则填充None，否则填写新价格stop_loss）
        "leverage": 整数，建议使用的杠杆（范围1-50；默认{baseline_lev}）
    }}
    """

    try:
        print(f"⏳ 正在调用{AI_PROVIDER.upper()} API ({AI_MODEL})...")
        model_name = os.getenv('DEEPSEEK_MODEL', AI_MODEL)
        response = ai_client.chat.completions.create(
            model=model_name,
            messages=[
                {"role": "system", "content": f"您是一位专业的交易员，专注于{TRADE_CONFIG['timeframe']}周期趋势分析。请结合K线形态和技术指标做出判断，并严格遵循JSON格式要求。"},
                {"role": "user", "content": prompt}
            ],
            stream=False,
            temperature=0.1,
            timeout=60.0
        )
        print("✓ API调用成功")
        state.web_data['ai_model_info']['status'] = 'connected'
        state.web_data['ai_model_info']['last_check'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        state.web_data['ai_model_info']['error_message'] = None

        if not response or not response.choices:
            print(f"❌ {AI_PROVIDER.upper()}返回空响应")
            state.web_data['ai_model_info']['status'] = 'error'
            state.web_data['ai_model_info']['error_message'] = '响应为空'
            return create_fallback_signal(price_data)

        result = response.choices[0].message.content
        if not result:
            print(f"❌ {AI_PROVIDER.upper()}返回空内容")
            return create_fallback_signal(price_data)

        print(f"\n{'='*60}")
        print(f"{AI_PROVIDER.upper()}原始回复:")
        print(result)
        print(f"{'='*60}\n")
        try:
            state.ai_raw_history.append(result)
            if len(state.ai_raw_history) > 3:
                state.ai_raw_history.pop(0)
        except Exception:
            pass

        start_idx = result.find('{')
        end_idx = result.rfind('}') + 1
        if start_idx != -1 and end_idx != 0:
            json_str = result[start_idx:end_idx]
            signal_data = safe_json_parse(json_str)
            if signal_data is None:
                print("⚠️ JSON解析失败，使用备用信号")
                signal_data = create_fallback_signal(price_data)
            else:
                print(f"✓ 成功解析AI决策: {signal_data.get('signal')} - {signal_data.get('confidence')}")
        else:
            print("⚠️ 未找到JSON格式，使用备用信号")
            signal_data = create_fallback_signal(price_data)

        try:
            sd = dict(signal_data) if isinstance(signal_data, dict) else {}
            if isinstance(sd.get('signal'), str):
                sd['signal'] = sd['signal'].strip().upper()
            trade_type = str(sd.get('trade_type', '') or '').strip().upper()
            if trade_type:
                sd['trade_type'] = trade_type
            pos_size = str(sd.get('position_size', '') or '').strip().upper()
            if pos_size in ('REDUCED', 'NORMAL', 'AGGRESSIVE'):
                sd['position_size'] = pos_size
            elif pos_size:
                sd['position_size'] = 'NORMAL'

            if (sd.get('take_profit') is None or sd.get('take_profit') == '') and sd.get('take_profit_price') is not None:
                sd['take_profit'] = sd.get('take_profit_price')
            if (sd.get('stop_loss') is None or sd.get('stop_loss') == '') and sd.get('stop_loss_price') is not None:
                sd['stop_loss'] = sd.get('stop_loss_price')

            tp_val = _parse_price_value(sd.get('take_profit'))
            sl_val = _parse_price_value(sd.get('stop_loss'))
            trl_val = _parse_price_value(sd.get('trailing_stop_loss'))
            if tp_val is not None:
                sd['take_profit'] = tp_val
                sd['take_profit_price'] = tp_val
            if sl_val is not None:
                sd['stop_loss'] = sl_val
                sd['stop_loss_price'] = sl_val
            if trl_val is not None:
                sd['trailing_stop_loss'] = trl_val

            valid_signals = {'BUY', 'SELL', 'HOLD'}
            if sd.get('signal') not in valid_signals:
                if trade_type in ('LONG', 'SHORT', 'HOLD'):
                    sd['signal'] = 'BUY' if trade_type == 'LONG' else ('SELL' if trade_type == 'SHORT' else 'HOLD')
                elif trade_type in ('ADD', 'REDUCE'):
                    try:
                        curpos = get_current_position()
                        if curpos and curpos.get('side') in ('long', 'short'):
                            sd['signal'] = 'BUY' if curpos['side'] == 'long' else 'SELL'
                        else:
                            sd['signal'] = 'HOLD'
                    except Exception:
                        sd['signal'] = 'HOLD'

            if isinstance(sd.get('confidence'), str):
                sd['confidence'] = sd['confidence'].strip().upper()
                if sd['confidence'] not in ('HIGH', 'MEDIUM', 'LOW'):
                    sd['confidence'] = 'MEDIUM'
            else:
                sd['confidence'] = 'MEDIUM'

            # 规范化杠杆字段（可选）
            try:
                lev = sd.get('leverage')
                if lev is not None:
                    lv = int(float(lev))
                    if lv <= 0:
                        raise ValueError('non-positive')
                    # 合理范围裁剪（OKX大多数USDT永续支持到50x或更高，这里保守控制）
                    lv = max(1, min(lv, 50))
                    sd['leverage'] = lv
                else:
                    # 无提供时不写入，后续按映射/基线处理
                    pass
            except Exception:
                try:
                    del sd['leverage']
                except Exception:
                    pass

            signal_data = sd
        except Exception:
            pass

        # 必填字段：允许 TP/SL 为空（None）以表示“不设置”
        required_fields = ['signal', 'reason', 'confidence']
        missing = [f for f in required_fields if f not in signal_data or signal_data.get(f) in (None, '')]
        if missing:
            print(f"⚠️ 缺少必需字段: {missing}，使用备用信号")
            signal_data = create_fallback_signal(price_data)
        else:
            # 对 None 的 TP/SL 采用“不设置”策略：置为 None，交由下游忽略
            if _parse_price_value(signal_data.get('take_profit')) is None:
                signal_data['take_profit'] = None
                signal_data['take_profit_price'] = None
            if _parse_price_value(signal_data.get('stop_loss')) is None:
                signal_data['stop_loss'] = None
                signal_data['stop_loss_price'] = None

        signal_data['timestamp'] = price_data['timestamp']
        state.signal_history.append(signal_data)
        if len(state.signal_history) > 30:
            state.signal_history.pop(0)

        signal_count = len([s for s in state.signal_history if s.get('signal') == signal_data['signal']])
        total_signals = len(state.signal_history)
        print(f"信号统计: {signal_data['signal']} (最近{total_signals}次中出现{signal_count}次)")

        if len(state.signal_history) >= 3:
            last_three = [s['signal'] for s in state.signal_history[-3:]]
            if len(set(last_three)) == 1:
                print(f"⚠️ 注意：连续3次{signal_data['signal']}信号")

        return signal_data
    except Exception as e:
        err_type, err_code = extract_error_info(e)
        code_text = err_code if err_code is not None else 'N/A'
        print(f"{AI_PROVIDER.upper()}分析失败: [{err_type} {code_text}] {e}")
        state.web_data['ai_model_info']['status'] = 'error'
        state.web_data['ai_model_info']['last_check'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        state.web_data['ai_model_info']['error_message'] = str(e)
        state.web_data['ai_model_info']['error_type'] = err_type
        state.web_data['ai_model_info']['error_code'] = err_code
        return create_fallback_signal(price_data)


def analyze_with_deepseek_with_retry(price_data, max_retries=2):
    now_ts = time.time()
    if now_ts < state.ai_backoff_until_ts and state.last_ai_decision_cache is not None:
        print("⏳ DeepSeek处于退避期，复用上次AI决策缓存")
        return state.last_ai_decision_cache
    if (now_ts - state.last_ai_call_ts) < float(state.AI_DECISION_INTERVAL or 0) and state.last_ai_decision_cache is not None:
        return state.last_ai_decision_cache
    state.last_ai_call_ts = now_ts
    for attempt in range(max_retries):
        try:
            signal_data = analyze_with_deepseek(price_data)
            if signal_data:
                state.last_ai_decision_cache = signal_data
                state.ai_backoff_until_ts = 0.0
                return signal_data
            print(f"第{attempt + 1}次尝试失败，进行重试...")
            time.sleep(2)
        except Exception as e:
            print(f"第{attempt + 1}次尝试异常: {e}")
            import traceback
            traceback.print_exc()
            if attempt == max_retries - 1:
                state.ai_backoff_until_ts = time.time() + 60
                return state.last_ai_decision_cache or create_fallback_signal(price_data)
            time.sleep(2)
    return state.last_ai_decision_cache or create_fallback_signal(price_data)



import os
import time
import json
import pandas as pd
import logging as logger
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
    get_open_interest_snapshot,
    get_current_funding_rate,
    get_all_positions,
    get_account_overview,
    format_positions_overview_for_prompt,
)
from .utils import safe_json_parse, extract_error_info, append_prompt_to_file


def _extract_ai_content(resp):
    try:
        # OpenAI Python SDK 对象（v1），带 choices 属性
        choices = getattr(resp, 'choices', None)
        if choices:
            ch0 = choices[0]
            msg = getattr(ch0, 'message', None)
            if msg is not None:
                content = getattr(msg, 'content', None)
                if content:
                    return content
                if isinstance(msg, dict) and msg.get('content'):
                    return msg.get('content')
            # 一些兼容实现可能提供 text 字段
            txt = getattr(ch0, 'text', None)
            if txt:
                return txt

        # 字典响应
        if isinstance(resp, dict):
            chs = resp.get('choices')
            if isinstance(chs, list) and chs:
                ch0 = chs[0]
                if isinstance(ch0, dict):
                    msg = ch0.get('message') or ch0.get('delta') or {}
                    if isinstance(msg, dict) and msg.get('content'):
                        return msg.get('content')
                    if 'text' in ch0 and ch0.get('text'):
                        return ch0.get('text')
            # 其他常见键
            for k in ('output_text', 'message', 'content', 'data'):
                v = resp.get(k)
                if isinstance(v, str) and v.strip():
                    return v

        # 纯字符串（可能是网关返回）
        if isinstance(resp, str):
            s = resp.strip()
            ls = s.lower()
            # 如果是 HTML 页面，视为无效响应
            if ls.startswith('<!doctype') or ls.startswith('<html') or '<html' in ls:
                return None
            if s.startswith('{') and s.endswith('}'):
                try:
                    return _extract_ai_content(json.loads(s))
                except Exception:
                    return s
            return s
    except Exception:
        return None
    return None


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
            timeout=float(os.getenv('AI_TEST_TIMEOUT_SECONDS', '15'))
        )
        content = _extract_ai_content(response)
        if content:
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


def _compute_total_unrealized_pnl_all_symbols(current_symbol: str, current_pos: dict) -> float:
    """聚合所有已配置交易对的浮动盈亏（含其他仓）。

    优先使用 state 符号桶缓存的 last_position，避免在生成 Prompt 时频繁触发私有接口限频。
    当前活跃符号的持仓直接使用传入的 current_pos。
    """
    try:
        total = 0.0
        # 当前符号先计入
        if current_pos and isinstance(current_pos.get('unrealized_pnl'), (int, float)):
            total += float(current_pos['unrealized_pnl'])

        symbols = TRADE_CONFIG.get('symbols', [current_symbol] if current_symbol else [])
        seen = set()
        for sym in symbols:
            try:
                if not sym or sym in seen:
                    continue
                seen.add(sym)
                if current_symbol and sym == current_symbol:
                    continue
                bucket = state.ensure_symbol_bucket(sym)
                pos = bucket.get('last_position') if isinstance(bucket, dict) else None
                if pos and (pos.get('unrealized_pnl') is not None):
                    total += float(pos.get('unrealized_pnl') or 0.0)
            except Exception:
                continue
        return float(total)
    except Exception:
        try:
            return float(current_pos.get('unrealized_pnl') or 0.0) if current_pos else 0.0
        except Exception:
            return 0.0


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
    def _fmt_number(val, default_str='N/A', precision=4):
        try:
            if val is None:
                return default_str
            f = float(val)
            fmt = f"{{:.{precision}f}}"
            return fmt.format(f)
        except Exception:
            return default_str

    def _fmt_list(lst, precision=4):
        try:
            if not lst:
                return "[]"
            fmt = f"{{:.{precision}f}}"
            return "[" + ", ".join(fmt.format(float(x)) for x in lst if x is not None) + "]"
        except Exception:
            try:
                return "[" + ", ".join(str(x) for x in (lst or [])) + "]"
            except Exception:
                return "[]"

    def build_structured_snapshot_prompt(pd_obj):
        tech = pd_obj.get('technical_data') or {}
        series = pd_obj.get('intraday_series') or {}
        bg4 = pd_obj.get('background_4h') or {}
        # 衍生品市场情绪（尽最大努力获取，失败回退 N/A）
        oi_latest = None
        oi_avg = None
        frate = None
        try:
            oi = get_open_interest_snapshot() or {}
            oi_latest = oi.get('latest')
        except Exception:
            pass
        try:
            fr = get_current_funding_rate() or {}
            frate = fr.get('rate')
        except Exception:
            pass

        part1 = (
            "第一部分：当前实时快照\n\n"
            f"当前价格： {pd_obj.get('price')}\n\n"
            f"20周期指数移动平均线： { _fmt_number(tech.get('ema_20'), precision=6)}\n\n"
            f"移动平均收敛散度： { _fmt_number(tech.get('macd'), precision=6)}\n\n"
            f"7周期相对强弱指数： { _fmt_number(tech.get('rsi_7'), precision=2)}\n\n"
        )

        part2 = (
            "第二部分：衍生品市场情绪\n\n"
            "未平仓合约：\n\n"
            f"最新值：{ _fmt_number(oi_latest, precision=0)}\n\n"
            f"平均值：{ _fmt_number(oi_avg, precision=0)}\n\n"
            f"资金费率： { _fmt_number(frate, precision=6)}\n\n"
        )

        part3 = (
            "第三部分：短期日内动态（每分钟，最旧 → 最新）\n\n"
            f"中间价序列： {_fmt_list(series.get('mid_prices'), precision=6)}\n\n"
            f"EMA指标（20周期）序列： {_fmt_list(series.get('ema20'), precision=6)}\n\n"
            f"MACD指标序列： {_fmt_list(series.get('macd'), precision=6)}\n\n"
            f"RSI指标（7周期）序列： {_fmt_list(series.get('rsi7'), precision=2)}\n\n"
            f"RSI指标（14周期）序列： {_fmt_list(series.get('rsi14'), precision=2)}\n\n"
        )

        part4 = (
            "第四部分：长期背景框架（基于4小时图）\n\n"
            "趋势对比：\n\n"
            f"20周期EMA： { _fmt_number(bg4.get('ema20'), precision=6)}\n\n"
            f"50周期EMA： { _fmt_number(bg4.get('ema50'), precision=6)}\n\n"
            "波动率对比（平均真实波幅）：\n\n"
            f"3周期ATR： { _fmt_number(bg4.get('atr3'), precision=6)}\n\n"
            f"14周期ATR： { _fmt_number(bg4.get('atr14'), precision=6)}\n\n"
            "成交量对比：\n\n"
            f"当前成交量： { _fmt_number(bg4.get('volume_current'), precision=0)}\n\n"
            f"平均成交量： { _fmt_number(bg4.get('volume_avg'), precision=0)}\n\n"
            f"MACD指标序列（4小时）： {_fmt_list(bg4.get('macd_series'), precision=6)}\n\n"
            f"RSI指标（14周期）序列（4小时）： {_fmt_list(bg4.get('rsi14_series'), precision=2)}\n\n"
        )

        return part1 + part2 + part3 + part4

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

    # 计算多合约总浮盈亏（含其他仓）
    try:
        _cur_symbol_for_total = TRADE_CONFIG.get('symbol', 'BTC/USDT:USDT')
    except Exception:
        _cur_symbol_for_total = 'BTC/USDT:USDT'
    total_unreal_all = _compute_total_unrealized_pnl_all_symbols(_cur_symbol_for_total, current_pos)

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
        # 新增：斐波纳契与枢轴位简述（Fib统一使用4H）
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
                mtf_text += f"【斐波纳契回撤(4H)】{fib_text}\n"
            if piv_text:
                mtf_text += f"【枢轴位(日)】{piv_text}\n"
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

    structured_snapshot = build_structured_snapshot_prompt(price_data)

    # 账户与全部仓位（优先）：先给出余额与总仓位清单，便于全局权衡
    try:
        _acct = get_account_overview() or {}
        _positions_all = get_all_positions(TRADE_CONFIG.get('symbols'))
        _positions_overview_text = format_positions_overview_for_prompt(_positions_all)
        account_block = (
            "【账户与全部持仓总览】\n"
            f"- USDT可用余额: {_acct.get('usdt_free', 0.0):,.2f}\n"
            f"- USDT总权益: {_acct.get('usdt_total', 0.0):,.2f}\n"
            f"- 当前全部持仓(按合约):\n{_positions_overview_text}\n"
        )
    except Exception:
        account_block = "【账户与全部持仓总览】数据暂不可用\n"

    prompt = f"""
    你是一个专业的加密货币交易分析师。请基于以下{TRADE_CONFIG['timeframe']} 周期数据分析，且我们的核心目标是：总仓位不断提升才是最大目标。
    ====
    {account_block}
    {structured_snapshot}

    {kline_text}

    {technical_analysis}

    {signal_text}

    {sentiment_text}  # 情绪分析（如有）

    {mtf_text}  # 多周期补充（如有）
    
    H关键支撑/阻力。

    【当前行情】
    - 当前价格: ${price_data['price']:,.2f}
    - 时间: {price_data['timestamp']}
    - 本K线最高: ${price_data['high']:,.2f}
    - 本K线最低: ${price_data['low']:,.2f}
    - 本K线成交量: {price_data['volume']:.2f} {base}
    - 价格变化: {price_data['price_change']:+.2f}%
    - 当前持仓: {position_text}{pnl_text}
    - 多合约总浮盈亏（含其他仓）: ${total_unreal_all:,.2f}

    
    【当前技术状况总览】
    - 整体趋势 (4H): {price_data['trend_analysis'].get('overall', 'N/A')}
    - 短期趋势 (15m): {price_data['trend_analysis'].get('short_term', 'N/A')}
    - RSI (15m): {price_data['technical_data'].get('rsi', 0):.1f} ({'超买' if price_data['technical_data'].get('rsi', 0) > 70 else '超卖' if price_data['technical_data'].get('rsi', 0) < 30 else '中性'})
    - MACD 方向 (15m): {price_data['trend_analysis'].get('macd', 'N/A')}
    - 布林带位置 (4H): {price_data['boll_4h'].get('bb_position', 0):.2%} ({'上部' if price_data['boll_4h'].get('bb_position', 0) > 0.7 else '下部' if price_data['boll_4h'].get('bb_position', 0) < 0.3 else '中部'})
    - 斐波纳契回撤 (4H): {price_data['levels_analysis'].get('fibonacci', {}).get('fib_23_6', 0):.2f} {price_data['levels_analysis'].get('fibonacci', {}).get('fib_38_2', 0):.2f} {price_data['levels_analysis'].get('fibonacci', {}).get('fib_50', 0):.2f} {price_data['levels_analysis'].get('fibonacci', {}).get('fib_61_8', 0):.2f} {price_data['levels_analysis'].get('fibonacci', {}).get('fib_78_6', 0):.2f}
    - 枢轴位 (日): {price_data['levels_analysis'].get('pivots', {}).get('pp', 0):.2f} {price_data['levels_analysis'].get('pivots', {}).get('r1', 0):.2f} {price_data['levels_analysis'].get('pivots', {}).get('s1', 0):.2f} {price_data['levels_analysis'].get('pivots', {}).get('r2', 0):.2f} {price_data['levels_analysis'].get('pivots', {}).get('s2', 0):.2f}
    
    【当前币种未成交订单】
    - 当前未成交普通订单（最多10条）：
    {open_orders_text}
    - 当前未成交策略订单（最多10条）：
    {algo_orders_text}
    ====

【执行规则】
- 止盈与止损均需基于4H级别的斐波纳契回撤与4
- 只有盈亏比大于2:1的才考虑建仓；如果已经建仓了，止盈止损都要以斐波纳契回撤、4H关键支撑/阻力为准；

**分批止盈策略**：
| 盈利水平 | 操作建议 | 备注 |
|---------|---------|------|
| 15% | 止盈1/3仓位 | 第一目标 |
| 25% | 再止盈1/3仓位 | 第二目标 |
| >25% | 移动止损保护 | 让利润奔跑 |

**技术参考位**：
- 阻力位、斐波那契扩展位
- 4H布林带上中下轨
- 避免强阻力区一次性止盈

### 3. 账户整体管理

**浮盈状态**（总盈亏>0）：
- 优先止盈或减仓
- 将浮盈落袋为安

**浮亏状态**（总盈亏≤0）：
- 不情绪化操作
- 仅调整保护性止损或保持原计划

### 4. 常见错误避免

- ✅ 使用具体美元价格，非固定百分比
- ✅ 结合市场情绪指标（恐惧贪婪指数）
  - 指数<20：可暂缓止损
  - 指数>80：提前止盈


    请用以下 JSON 格式回复（一次性返回所有币种的决策）：
    {{
        "objective": "INCREASE_TOTAL_POSITION_SIZE",
        "decisions": [
            {{
                "symbol": "{symbol}",
                "signal": "BUY|SELL|HOLD",
                "reason": "针对该币种的操作理由，是否做多/做空/持仓/加仓/减仓。",
                "stop_loss": 具体价格（若不设置请返回 null），
                "take_profit": 具体价格（若不设置请返回 null），
                "confidence": "HIGH|MEDIUM|LOW",
                "position_size": "REDUCED|NORMAL|AGGRESSIVE",
                "trade_type": "LONG|SHORT|HOLD|ADD|REDUCE",
                "take_profit_price": 具体价格或 null（与take_profit不同字段同义兜底），
                "stop_loss_price": 具体价格或 null（与stop_loss不同字段同义兜底），
                "leverage": 整数，建议使用的杠杆（范围1-50；默认{baseline_lev}）
            }}
            // 其余监控币种按相同结构逐一给出
        ]
    }}

    您的目标是最大化利润以动态调整所有仓位，并严格按照Json格式输出。
    """

    # 记录与调用的统一消息体
    messages = [
        {"role": "system", "content": f"您是一位专业的交易员，专注于{TRADE_CONFIG['timeframe']}周期趋势分析。请结合K线形态和技术指标做出判断，并严格遵循JSON格式要求。"},
        {"role": "user", "content": prompt}
    ]

    # 写入 prompts.jsonl 记录（含 messages / 字符长度）
    try:
        append_prompt_to_file({
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'symbol': symbol,
            'timeframe': TRADE_CONFIG['timeframe'],
            'structured_snapshot': structured_snapshot,
            'full_prompt': prompt,
            'messages': messages,
            'prompt_char_len': len(prompt),
            'status': 'sent'
        })
    except Exception:
        pass

    try:
        print(f"⏳ 正在调用{AI_PROVIDER.upper()} API ({AI_MODEL})...")
        model_name = os.getenv('AI_MODEL', AI_MODEL)
        response = ai_client.chat.completions.create(
            model=model_name,
            messages=messages,
        )
        logger.debug(f"AI API Response: {response}")
        result = _extract_ai_content(response)
        if not result:
            try:
                resp_type = type(response).__name__
                preview_text = None
                if isinstance(response, dict):
                    try:
                        preview_text = json.dumps(response)[:600]
                    except Exception:
                        preview_text = str(response)
                else:
                    dumped = None
                    try:
                        md = getattr(response, 'model_dump_json', None)
                        if callable(md):
                            dumped = md()
                    except Exception:
                        dumped = None
                    if not dumped:
                        try:
                            dumped = getattr(response, 'json', None)
                            if callable(dumped):
                                dumped = dumped()
                        except Exception:
                            dumped = None
                    if not dumped:
                        dumped = str(response)
                    preview_text = str(dumped)
                preview = (preview_text or '')
                preview = preview.replace('\n', ' ')[:600]
                print(f"❌ {AI_PROVIDER.upper()}返回空响应 (type={resp_type}, preview={preview})")
            except Exception:
                print(f"❌ {AI_PROVIDER.upper()}返回空响应 (无法打印详细信息)")
            state.web_data['ai_model_info']['status'] = 'error'
            state.web_data['ai_model_info']['error_message'] = '响应为空'
            return create_fallback_signal(price_data)

        if not isinstance(result, str) or not result.strip():
            print(f"❌ {AI_PROVIDER.upper()}返回空内容")
            return create_fallback_signal(price_data)

        print("✓ API调用成功")
        state.web_data['ai_model_info']['status'] = 'connected'
        state.web_data['ai_model_info']['last_check'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        state.web_data['ai_model_info']['error_message'] = None

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

            # 规范化合约张数（size/sz），若提供则转为正整数
            try:
                raw_size = sd.get('size')
                if raw_size is None:
                    raw_size = sd.get('sz')
                if raw_size is not None:
                    szv = int(float(raw_size))
                    if szv > 0:
                        sd['size'] = szv
                    else:
                        # 非法或非正，移除
                        sd.pop('size', None)
                else:
                    sd.pop('size', None)
            except Exception:
                sd.pop('size', None)

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


def build_structured_snapshot_for_any(pd_obj: dict) -> str:
    """复用单币种 structured_snapshot 的结构，供组合级每币种块使用。"""
    try:
        def _fmt_number(val, default_str='N/A', precision=4):
            try:
                if val is None:
                    return default_str
                f = float(val)
                fmt = f"{{:.{precision}f}}"
                return fmt.format(f)
            except Exception:
                return default_str

        def _fmt_list(lst, precision=4):
            try:
                if not lst:
                    return "[]"
                fmt = f"{{:.{precision}f}}"
                return "[" + ", ".join(fmt.format(float(x)) for x in lst if x is not None) + "]"
            except Exception:
                try:
                    return "[" + ", ".join(str(x) for x in (lst or [])) + "]"
                except Exception:
                    return "[]"

        tech = pd_obj.get('technical_data') or {}
        series = pd_obj.get('intraday_series') or {}
        bg4 = pd_obj.get('background_4h') or {}

        # 衍生品市场情绪（尽最大努力获取，失败回退 N/A）
        oi_latest = None
        oi_avg = None
        frate = None
        try:
            oi = get_open_interest_snapshot() or {}
            oi_latest = oi.get('latest')
        except Exception:
            pass
        try:
            fr = get_current_funding_rate() or {}
            frate = fr.get('rate')
        except Exception:
            pass

        part1 = (
            "第一部分：当前实时快照\n\n"
            f"当前价格： {pd_obj.get('price')}\n\n"
            f"20周期指数移动平均线： { _fmt_number(tech.get('ema_20'), precision=6)}\n\n"
            f"移动平均收敛散度： { _fmt_number(tech.get('macd'), precision=6)}\n\n"
            f"7周期相对强弱指数： { _fmt_number(tech.get('rsi_7'), precision=2)}\n\n"
        )

        part2 = (
            "第二部分：衍生品市场情绪\n\n"
            "未平仓合约：\n\n"
            f"最新值：{ _fmt_number(oi_latest, precision=0)}\n\n"
            f"平均值：{ _fmt_number(oi_avg, precision=0)}\n\n"
            f"资金费率： { _fmt_number(frate, precision=6)}\n\n"
        )

        part3 = (
            "第三部分：短期日内动态（每分钟，最旧 → 最新）\n\n"
            f"中间价序列： { _fmt_list(series.get('mid_prices'), precision=6)}\n\n"
            f"EMA指标（20周期）序列： { _fmt_list(series.get('ema20'), precision=6)}\n\n"
            f"MACD指标序列： { _fmt_list(series.get('macd'), precision=6)}\n\n"
            f"RSI指标（7周期）序列： { _fmt_list(series.get('rsi7'), precision=2)}\n\n"
            f"RSI指标（14周期）序列： { _fmt_list(series.get('rsi14'), precision=2)}\n\n"
        )

        part4 = (
            "第四部分：长期背景框架（基于4小时图）\n\n"
            "趋势对比：\n\n"
            f"20周期EMA： { _fmt_number(bg4.get('ema20'), precision=6)}\n\n"
            f"50周期EMA： { _fmt_number(bg4.get('ema50'), precision=6)}\n\n"
            "波动率对比（平均真实波幅）：\n\n"
            f"3周期ATR： { _fmt_number(bg4.get('atr3'), precision=6)}\n\n"
            f"14周期ATR： { _fmt_number(bg4.get('atr14'), precision=6)}\n\n"
            "成交量对比：\n\n"
            f"当前成交量： { _fmt_number(bg4.get('volume_current'), precision=0)}\n\n"
            f"平均成交量： { _fmt_number(bg4.get('volume_avg'), precision=0)}\n\n"
            f"MACD指标序列（4小时）： { _fmt_list(bg4.get('macd_series'), precision=6)}\n\n"
            f"RSI指标（14周期）序列（4小时）： { _fmt_list(bg4.get('rsi14_series'), precision=2)}\n\n"
        )

        return part1 + part2 + part3 + part4
    except Exception:
        return "结构化快照不可用"

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



def _build_symbol_snapshot_for_portfolio(pd_obj: dict, baseline_lev: int) -> str:
    try:
        sym = str(pd_obj.get('symbol') or TRADE_CONFIG.get('symbol') or 'N/A')
        price = pd_obj.get('price')
        trend = pd_obj.get('trend_analysis', {})
        tech = pd_obj.get('technical_data', {})
        levels = pd_obj.get('levels_analysis', {}) or {}
        fib = levels.get('fibonacci') or {}
        boll4 = pd_obj.get('boll_4h') or {}
        return (
            f"[{sym}] 价:{price} | 趋势(4H):{trend.get('overall','N/A')} | RSI(15m):{tech.get('rsi', 0):.1f} | "
            f"BOLL4H pos:{boll4.get('bb_position', 0):.2%} | Fib4H 23.6/38.2/50/61.8:{fib.get('fib_23_6',0):.2f}/{fib.get('fib_38_2',0):.2f}/{fib.get('fib_50',0):.2f}/{fib.get('fib_61_8',0):.2f} | 基线杠杆:{baseline_lev}x"
        )
    except Exception:
        return f"[{pd_obj.get('symbol','N/A')}] 数据不足"


def _build_symbol_memory_text(sym: str) -> str:
    try:
        b = state.ensure_symbol_bucket(sym)
        # 最近信号
        last_sig = 'N/A'
        try:
            sh = b.get('signal_history') or []
            if sh:
                ls = sh[-1]
                last_sig = f"{ls.get('signal','N/A')}/{ls.get('confidence','N/A')}"
        except Exception:
            pass
        # 预期TP/SL
        tpsl = b.get('tpsl_expected') or {}
        tp = tpsl.get('tp')
        sl = tpsl.get('sl')
        # 上次交易时间
        ltt = b.get('last_trade_time')
        try:
            ltt_s = ltt.strftime('%Y-%m-%d %H:%M:%S') if ltt else 'None'
        except Exception:
            ltt_s = str(ltt) if ltt else 'None'
        # 加仓计数
        padd = f"long_adds:{b.get('pyramid_adds_long',0)} short_adds:{b.get('pyramid_adds_short',0)}"
        # 近期AI回复预览（各自符号）
        ai_hist = b.get('ai_raw_history') or []
        previews = []
        try:
            for raw in ai_hist[-2:]:
                s = str(raw or '')
                if len(s) > 300:
                    s = s[:300] + '...'
                previews.append(s.replace('\n', ' '))
        except Exception:
            previews = []
        ai_prev = " | ".join(previews) if previews else '无'
        return (
            f"- 最近信号: {last_sig}\n"
            f"- 上次交易时间: {ltt_s}\n"
            f"- 预期TP/SL: {tp} / {sl}\n"
            f"- 加仓计数: {padd}\n"
            f"- 近期AI回复: {ai_prev}"
        )
    except Exception:
        return "- 内存状态获取失败"


def _build_detailed_symbol_block(sym: str, pd_obj: dict, total_unreal_all: float) -> str:
    try:
        # 切换活跃符号，确保挂单等接口指向对应 instId
        try:
            from .state import switch_active_symbol as _switch
            _switch(sym)
            TRADE_CONFIG['symbol'] = sym
        except Exception:
            TRADE_CONFIG['symbol'] = sym

        # 使用桶缓存的持仓，避免多次私有查询
        b = state.ensure_symbol_bucket(sym)
        curpos = b.get('last_position') or None
        try:
            upnl = float((curpos or {}).get('unrealized_pnl') or 0.0)
        except Exception:
            upnl = 0.0
        position_text = (
            "无持仓" if not curpos else f"{curpos.get('side')}仓, 数量: {curpos.get('size')}, 盈亏: {upnl:.2f}USDT"
        )

        # 未成交订单
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

        trend = pd_obj.get('trend_analysis', {})
        tech = pd_obj.get('technical_data', {})
        boll4 = pd_obj.get('boll_4h', {}) or {}
        levels = pd_obj.get('levels_analysis', {}) or {}
        fib = levels.get('fibonacci', {}) or {}
        piv = levels.get('pivots', {}) or {}
        base = sym.split('/')[0] if '/' in sym else sym
        mem_text = _build_symbol_memory_text(sym)
        structured_snapshot = build_structured_snapshot_for_any(pd_obj)

        return (
            "====\n"
            f"【币种】{sym}\n"
            f"【该币种内存状态】\n{mem_text}\n\n"
            f"【结构化快照】\n{structured_snapshot}\n\n"
            "【当前行情】\n"
            f"- 当前价格: ${pd_obj.get('price'):,.2f}\n"
            f"- 时间: {pd_obj.get('timestamp')}\n"
            f"- 本K线最高: ${pd_obj.get('high'):,.2f}\n"
            f"- 本K线最低: ${pd_obj.get('low'):,.2f}\n"
            f"- 本K线成交量: {pd_obj.get('volume'):.2f} {base}\n"
            f"- 价格变化: {pd_obj.get('price_change'):+.2f}%\n"
            f"- 当前持仓: {position_text}\n"
            f"- 多合约总浮盈亏（含其他仓）: ${total_unreal_all:,.2f}\n\n"
            "【当前技术状况总览】\n"
            f"- 整体趋势 (4H): {trend.get('overall', 'N/A')}\n"
            f"- 短期趋势 (15m): {trend.get('short_term', 'N/A')}\n"
            f"- RSI (15m): {tech.get('rsi', 0):.1f}\n"
            f"- MACD 方向 (15m): {trend.get('macd', 'N/A')}\n"
            f"- 布林带位置 (4H): {boll4.get('bb_position', 0):.2%}\n"
            f"- 斐波纳契回撤 (4H): {fib.get('fib_23_6', 0):.2f} {fib.get('fib_38_2', 0):.2f} {fib.get('fib_50', 0):.2f} {fib.get('fib_61_8', 0):.2f} {fib.get('fib_78_6', 0):.2f}\n"
            f"- 枢轴位 (日): {piv.get('pp', 0):.2f} {piv.get('r1', 0):.2f} {piv.get('s1', 0):.2f} {piv.get('r2', 0):.2f} {piv.get('s2', 0):.2f}\n\n"
            "【当前币种未成交订单】\n"
            f"- 当前未成交普通订单（最多10条）：\n{open_orders_text}\n"
            f"- 当前未成交策略订单（最多10条）：\n{algo_orders_text}\n"
            "===="
        )
    except Exception:
        return f"====\n【币种】{sym}\n数据不足\n===="

def analyze_portfolio_with_deepseek(symbol_to_price_data: dict) -> list:
    """组合级分析：一次提交所有币种信息与账户/仓位，返回 decisions 列表。"""
    try:
        symbols = list(symbol_to_price_data.keys())
        if not symbols:
            return []

        # 账户与全部仓位模块
        try:
            acct = get_account_overview() or {}
            positions_all = get_all_positions(TRADE_CONFIG.get('symbols'))
            positions_overview_text = format_positions_overview_for_prompt(positions_all)
            account_block = (
                "【账户与全部持仓总览】\n"
                f"- USDT可用余额: {acct.get('usdt_free', 0.0):,.2f}\n"
                f"- USDT总权益: {acct.get('usdt_total', 0.0):,.2f}\n"
                f"- 当前全部持仓(按合约):\n{positions_overview_text}\n"
            )
        except Exception:
            account_block = "【账户与全部持仓总览】数据暂不可用\n"

        # 计算组合总浮盈亏（用于各币种块展示）
        try:
            total_unreal_all = 0.0
            try:
                _sum_src = positions_all or []
            except Exception:
                _sum_src = []
            for p in _sum_src:
                try:
                    total_unreal_all += float(p.get('unrealized_pnl') or 0.0)
                except Exception:
                    continue
        except Exception:
            total_unreal_all = 0.0

        # 为每个币种构建详细块（含内存状态、行情、技术、挂单）
        per_symbol_blocks = []
        for sym in symbols:
            pd_obj = symbol_to_price_data.get(sym) or {}
            per_symbol_blocks.append(_build_detailed_symbol_block(sym, pd_obj, total_unreal_all))
        per_symbol_block = "\n".join(per_symbol_blocks)

        prompt = f"""
您是资深加密衍生品交易员。我们一次性提供账户余额、全部持仓与多个币种的关键快照。请在“总仓位不断提升才是最大目标”的前提下，给出每个币种的交易决策，并严格按JSON输出。

{account_block}

【多币种详细快照】
{per_symbol_block}

请输出：
{{
  "objective": "INCREASE_TOTAL_POSITION_SIZE",
  "decisions": [
    {{
      "symbol": "必须为以下之一: {', '.join(symbols)}",
      "signal": "BUY|SELL|HOLD",
      "size": 合约数量，（必填，如果当前未成交策略订单有合理的大小，则填充该大小）
      "reason": "当前币种为***，说明为什么做出这个决定，建议做多还是做空，还是继续持仓，加仓还是减仓。",
      "stop_loss": 具体价格，（必填，如果当前未成交策略订单有合理的价格，则填充该价格，注意要向下浮动部分）
      "take_profit": 具体价格，（必填，如果当前未成交策略订单有合理的价格，则填充该价格，注意要向上浮动部分）
      "confidence": "HIGH|MEDIUM|LOW",
      "take_profit_price": 具体价格,（可选，如果take_profit的与当前未成交策略订单当中相等，则填充None，否则填写新价格take_profit）
      "stop_loss_price": 具体价格,（可选，注意stop_loss不同，如果stop_loss的与当前未成交策略订单当中相等，则填充None，否则填写新价格stop_loss）
      "leverage": 整数(1-50) 或省略
    }}
    // 其余币种按相同结构追加
  ]
}}

    您的目标是动态调整所有仓位以最大化利润（初始是$1000的，但现在亏得差不多了），请你严格按照Json格式输出。
"""

        # 记录完整Prompt（含 messages / 字符长度）
        try:
            portfolio_messages = [
                {"role": "system", "content": "您是一位专业的交易员，负责编排多币种持仓以提升总仓位。请结合多币种数据并严格输出JSON。"},
                {"role": "user", "content": prompt}
            ]
            append_prompt_to_file({
                'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'symbols': symbols,
                'timeframe': TRADE_CONFIG['timeframe'],
                'full_prompt': prompt,
                'messages': portfolio_messages,
                'prompt_char_len': len(prompt),
                'portfolio': True,
                'status': 'sent'
            })
        except Exception:
            portfolio_messages = [
                {"role": "system", "content": "您是一位专业的交易员，负责编排多币种持仓以提升总仓位。请结合多币种数据并严格输出JSON。"},
                {"role": "user", "content": prompt}
            ]
            pass

        print(f"⏳ 正在调用{AI_PROVIDER.upper()} API ({AI_MODEL})... [portfolio]")
        model_name = os.getenv('AI_MODEL', AI_MODEL)
        response = ai_client.chat.completions.create(
            model=model_name,
            messages=portfolio_messages
        )
        raw = _extract_ai_content(response)
        if not raw or not isinstance(raw, str):
            print("❌ 组合级AI响应为空")
            return []

        print(f"\n{'='*60}")
        print(f"{AI_PROVIDER.upper()} 组合级原始回复:")
        print(raw)
        print(f"{'='*60}\n")
        try:
            state.ai_raw_history.append(raw)
            if len(state.ai_raw_history) > 3:
                state.ai_raw_history.pop(0)
        except Exception:
            pass

        # 尝试提取JSON（优先数组）
        content = raw.strip()
        decisions_obj = None
        try:
            if '[' in content and ']' in content:
                start = content.find('[')
                end = content.rfind(']') + 1
                arr_str = content[start:end]
                val = safe_json_parse(arr_str)
                if isinstance(val, list):
                    decisions_obj = {'decisions': val}
        except Exception:
            decisions_obj = None
        if decisions_obj is None:
            try:
                # 回退：提取最外层花括号
                start = content.find('{')
                end = content.rfind('}') + 1
                if start != -1 and end > start:
                    obj_str = content[start:end]
                    val = safe_json_parse(obj_str)
                    if isinstance(val, dict):
                        decisions_obj = val
            except Exception:
                decisions_obj = None
        if not isinstance(decisions_obj, dict):
            print("⚠️ 组合级JSON解析失败")
            return []

        # 归一化为 decisions 列表
        if isinstance(decisions_obj.get('decisions'), list):
            decisions = decisions_obj['decisions']
        elif isinstance(decisions_obj, list):
            decisions = decisions_obj
        elif all(k in decisions_obj for k in ('symbol', 'signal')):
            decisions = [decisions_obj]
        else:
            print("⚠️ 未发现decisions，返回空")
            return []

        norm_list = []
        for d in decisions:
            if not isinstance(d, dict):
                continue
            item = dict(d)
            # 规范化符号
            sym = str(item.get('symbol') or '').strip() or None
            if not sym or sym not in symbols:
                # 跳过未知符号
                continue
            item['symbol'] = sym

            # 信号/置信度/类型
            sig = str(item.get('signal') or 'HOLD').strip().upper()
            if sig not in ('BUY', 'SELL', 'HOLD'):
                sig = 'HOLD'
            item['signal'] = sig
            conf = str(item.get('confidence') or 'MEDIUM').strip().upper()
            if conf not in ('HIGH', 'MEDIUM', 'LOW'):
                conf = 'MEDIUM'
            item['confidence'] = conf

            # 价格字段归一化
            tp_val = _parse_price_value(item.get('take_profit'))
            sl_val = _parse_price_value(item.get('stop_loss'))
            tpp = _parse_price_value(item.get('take_profit_price')) if item.get('take_profit_price') is not None else None
            slp = _parse_price_value(item.get('stop_loss_price')) if item.get('stop_loss_price') is not None else None
            if tp_val is None:
                tp_val = tpp
            if sl_val is None:
                sl_val = slp
            item['take_profit'] = tp_val
            item['stop_loss'] = sl_val
            item['take_profit_price'] = tp_val
            item['stop_loss_price'] = sl_val

            # 杠杆
            try:
                lv = item.get('leverage')
                if lv is not None:
                    lv = int(float(lv))
                    lv = max(1, min(lv, 50))
                    item['leverage'] = lv
            except Exception:
                try:
                    del item['leverage']
                except Exception:
                    pass

            # 合约张数（size/sz）
            try:
                raw_size = item.get('size')
                if raw_size is None:
                    raw_size = item.get('sz')
                if raw_size is not None:
                    szv = int(float(raw_size))
                    if szv > 0:
                        item['size'] = szv
                    else:
                        item.pop('size', None)
                else:
                    item.pop('size', None)
            except Exception:
                item.pop('size', None)

            # 理由
            if not isinstance(item.get('reason'), str):
                item['reason'] = ''

            norm_list.append(item)

        return norm_list
    except Exception as e:
        err_type, err_code = extract_error_info(e)
        code_text = err_code if err_code is not None else 'N/A'
        print(f"组合级分析失败: [{err_type} {code_text}] {e}")
        return []


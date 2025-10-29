import os
import time
import json
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

    prompt = f"""
    你是一个专业的加密货币交易分析师。请基于以下{symbol} {TRADE_CONFIG['timeframe']} 周期数据进行分析：

    {kline_text}

    {technical_analysis}

    {signal_text}

    {sentiment_text}  # 情绪分析（如有）

    {mtf_text}  # 多周期补充（如有）
    【执行规则】止盈与止损均需基于4H级别的斐波纳契回撤与4H关键支撑/阻力。

    【当前行情】
    - 当前价格: ${price_data['price']:,.2f}
    - 时间: {price_data['timestamp']}
    - 本K线最高: ${price_data['high']:,.2f}
    - 本K线最低: ${price_data['low']:,.2f}
    - 本K线成交量: {price_data['volume']:.2f} {base}
    - 价格变化: {price_data['price_change']:+.2f}%
    - 当前持仓: {position_text}{pnl_text}
    - 多合约总浮盈亏（含其他仓）: ${total_unreal_all:,.2f}
    - 当前未成交普通订单（最多10条）：
    {open_orders_text}
    - 当前未成交策略订单（最多10条）：
    {algo_orders_text}
    
    
    【当前技术状况总览】
    - 整体趋势 (4H): {price_data['trend_analysis'].get('overall', 'N/A')}
    - 短期趋势 (15m): {price_data['trend_analysis'].get('short_term', 'N/A')}
    - RSI (15m): {price_data['technical_data'].get('rsi', 0):.1f} ({'超买' if price_data['technical_data'].get('rsi', 0) > 70 else '超卖' if price_data['technical_data'].get('rsi', 0) < 30 else '中性'})
    - MACD 方向 (15m): {price_data['trend_analysis'].get('macd', 'N/A')}
    - 布林带位置 (4H): {price_data['boll_4h'].get('bb_position', 0):.2%} ({'上部' if price_data['boll_4h'].get('bb_position', 0) > 0.7 else '下部' if price_data['boll_4h'].get('bb_position', 0) < 0.3 else '中部'})
    - 斐波纳契回撤 (4H): {price_data['levels_analysis'].get('fibonacci', {}).get('fib_23_6', 0):.2f} {price_data['levels_analysis'].get('fibonacci', {}).get('fib_38_2', 0):.2f} {price_data['levels_analysis'].get('fibonacci', {}).get('fib_50', 0):.2f} {price_data['levels_analysis'].get('fibonacci', {}).get('fib_61_8', 0):.2f} {price_data['levels_analysis'].get('fibonacci', {}).get('fib_78_6', 0):.2f}
    - 枢轴位 (日): {price_data['levels_analysis'].get('pivots', {}).get('pp', 0):.2f} {price_data['levels_analysis'].get('pivots', {}).get('r1', 0):.2f} {price_data['levels_analysis'].get('pivots', {}).get('s1', 0):.2f} {price_data['levels_analysis'].get('pivots', {}).get('r2', 0):.2f} {price_data['levels_analysis'].get('pivots', {}).get('s2', 0):.2f}

    【前两次AI原始回复（供一致性参考）】
    {prev_ai_raw}

# 交易策略评估与优化建议

## 一、策略合适性分析

### 核心优势
- **风险分散良好**：单一方向/行业/币种持仓≤20%，有效降低非系统性风险
- **杠杆管理灵活**：根据市场状况动态调整，避免过度暴露
- **止盈止损规则具体**：强调盈亏比≥2:1，使用具体价格而非百分比，减少主观决策
- **纪律性强**：逆向思维操作，避免情绪化交易

### 需要调整的细节

#### 1. 长影针分析修正
**原策略问题**：
> "长影针是阳线→可能反转；长影针是阴线→可能继续上涨"

**正确解读**：
- **长上影线**（高价区）：上涨受阻，可能下跌反转（尤其伴随放量）
- **长下影线**（低价区）：下跌受阻，可能上涨反转

**建议修正**：
- 出现长上影线时警惕回调
- 出现长下影线时关注反弹
- 需结合其他指标确认信号

#### 2. 止损止盈浮动幅度
**问题**：固定5%浮动可能不合理
- 浮动过大→止损失效或止盈过早
- 忽略市场波动差异

**优化建议**：
- 使用ATR（平均真实波幅）指标动态调整浮动范围
- 高波动品种扩大缓冲，低波动品种缩小缓冲

#### 3. 其他合理规则
- 单次调仓≤15%
- 同时操作≤3个仓位
- 防止过度交易

## 二、止盈止损设置指南

### 1. 止损设置原则

**技术依据**：
- 4H布林带、关键支撑/阻力位
- 斐波那契回撤位、均线（50/200日）
- 避免15分钟布林带上下轨（噪音大）

**具体方法**：

多头仓位：
止损位 = 关键支撑下方 + 波动缓冲
示例：BTC买入价50,000，支撑48,000
      止损设$47,500（支撑下方约1%）

空头仓位：
止损位 = 关键阻力上方 + 波动缓冲


**浮动缓冲调整**：
- 根据品种波动性设定（如BTC日均波动3%→缓冲2-3%）
- 使用ATR指标量化波动幅度

### 2. 止盈设置原则

**盈亏比优先**：
- 风险回报比≥2:1
- 止损距离$500 → 止盈距离≥$1,000

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

**示例**：

ETH买入价3,000，止损2,800（风险$200）
第一止盈：3,400（盈利400，盈亏比2:1）
第二止盈：$3,600（参考前期阻力）


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
        model_name = os.getenv('AI_MODEL', AI_MODEL)
        response = ai_client.chat.completions.create(
            model=model_name,
            messages=[
                {"role": "system", "content": f"您是一位专业的交易员，专注于{TRADE_CONFIG['timeframe']}周期趋势分析。请结合K线形态和技术指标做出判断，并严格遵循JSON格式要求。"},
                {"role": "user", "content": prompt}
            ],
            stream=False,
            temperature=0.1,
            timeout=float(os.getenv('AI_REQUEST_TIMEOUT_SECONDS', '60'))
        )
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



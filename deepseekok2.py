import os
import time
import schedule
from openai import OpenAI
import ccxt
import pandas as pd
import re
from dotenv import load_dotenv
import json
import requests
from datetime import datetime, timedelta
import math
load_dotenv()
import config

# 初始化AI客户端
# 支持DeepSeek和阿里百炼Qwen
AI_PROVIDER = os.getenv('AI_PROVIDER', 'deepseek').lower()  # 'deepseek' 或 'qwen'

if AI_PROVIDER == 'qwen':
    # 阿里百炼Qwen客户端
    ai_client = OpenAI(
        api_key=os.getenv('DASHSCOPE_API_KEY'),
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1"
    )
    AI_MODEL = "qwen-max"
    print(f"使用AI模型: 阿里百炼 {AI_MODEL}")
else:
    # DeepSeek客户端（默认）
    ai_client = OpenAI(
        api_key=os.getenv('DEEPSEEK_API_KEY'),
        base_url="https://api.deepseek.com"
    )
    AI_MODEL = "deepseek-chat"
    print(f"使用AI模型: DeepSeek {AI_MODEL}")

# 保持向后兼容
deepseek_client = ai_client

# 初始化OKX交易所
exchange = ccxt.okx({
    'options': {
        'defaultType': 'swap',  # OKX使用swap表示永续合约
    },
    'apiKey': os.getenv('OKX_API_KEY'),
    'secret': os.getenv('OKX_SECRET'),
    'password': os.getenv('OKX_PASSWORD'),  # OKX需要交易密码
})

# 交易参数配置 - 结合两个版本的优点
TRADE_CONFIG = {
    'symbol': 'BTC/USDT:USDT',  # OKX的合约符号格式
    'amount': 0.001,  # 交易数量 (BTC)
    'leverage': 10,  # 杠杆倍数
    'timeframe': config.TIMEFRAME,  # 使用配置的时间框架
    'test_mode': False,  # 测试模式
    'data_points': 96,  # 24小时数据（96根15分钟K线）
    'analysis_periods': {
        'short_term': 20,  # 短期均线
        'medium_term': 50,  # 中期均线
        'long_term': 96  # 长期趋势
    }
}

# 将BTC数量换算为OKX合约张数（size），确保不小于1张
def btc_amount_to_okx_contracts(btc_amount: float) -> int:
    try:
        market = exchange.market(TRADE_CONFIG['symbol'])
        # 优先取统一字段contractSize，回退到原始字段ctVal
        contract_size = market.get('contractSize') or float(market.get('info', {}).get('ctVal', 0))
        if not contract_size or contract_size <= 0:
            # 常见面值：BTC/USDT:USDT 多为 0.001 BTC/张（不同账户可能不同），兜底
            contract_size = 0.001
        contracts = math.floor((btc_amount / contract_size) + 1e-9)
        return max(1, int(contracts))
    except Exception:
        return 1


def get_contract_size_btc() -> float:
    try:
        market = exchange.market(TRADE_CONFIG['symbol'])
        contract_size = market.get('contractSize') or float(market.get('info', {}).get('ctVal', 0))
        if not contract_size or contract_size <= 0:
            contract_size = 0.001
        return float(contract_size)
    except Exception:
        return 0.001


def estimate_required_margin_usdt(contracts: int, mark_price_usdt: float, leverage: float) -> float:
    ct_size_btc = get_contract_size_btc()
    notional = contracts * ct_size_btc * mark_price_usdt
    return (notional / max(leverage, 1.0)) * 1.05  # 加5%裕度

# 全局变量存储历史数据
price_history = []
signal_history = []
position = None
last_trade_time = None  # 记录上次交易时间
MIN_TRADE_INTERVAL = int(config.TRADE_MIN_INTERVAL_SECONDS)  # 最小交易间隔（秒），防止过于频繁交易

# 私有接口（余额/持仓）更新节流，避免频繁调用导致限流
PRIVATE_UPDATE_INTERVAL = float(config.PRIVATE_UPDATE_INTERVAL_SECONDS)  # 秒，仅每N秒刷新一次余额和持仓
last_private_update_ts = 0.0

# 技术分析/情绪数据缓存，降低外部与公共接口压力
ANALYSIS_UPDATE_INTERVAL = float(config.ANALYSIS_UPDATE_INTERVAL_SECONDS)  # 秒，整套技术指标刷新间隔
last_analysis_ts = 0.0
last_price_data_cache = None

SENTIMENT_TTL = float(config.SENTIMENT_CACHE_TTL_SECONDS)  # 秒，情绪数据缓存时间
_sentiment_cache = { 'ts': 0.0, 'data': None }

# AI 决策节流与缓存，降低DeepSeek调用频率，减少超时
AI_DECISION_INTERVAL = float(config.AI_DECISION_MIN_INTERVAL_SECONDS)  # 秒，最小AI调用间隔
last_ai_call_ts = 0.0
last_ai_decision_cache = None
ai_backoff_until_ts = 0.0  # 出现超时后退避一段时间

# Web展示相关的全局数据存储
web_data = {
    'account_info': {},
    'current_position': None,
    'current_price': 0,
    'trade_history': [],
    'ai_decisions': [],
    'performance': {
        'total_profit': 0,
        'win_rate': 0,
        'total_trades': 0,
        'wins': 0,
        'losses': 0
    },
    'kline_data': [],
    'profit_curve': [],  # 收益曲线数据
    'last_update': None,
    'ai_model_info': {
        'provider': AI_PROVIDER,
        'model': AI_MODEL,
        'status': 'unknown',  # unknown, connected, error
        'last_check': None,
        'error_message': None
    }
}

# AI 决策持久化文件（JSONL，每行一个决策）
AI_DECISIONS_LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'ai_decisions.jsonl')
os.makedirs(os.path.dirname(AI_DECISIONS_LOG_PATH), exist_ok=True)

def append_ai_decision_to_file(decision: dict) -> None:
    """将AI决策追加写入本地JSONL文件。"""
    try:
        with open(AI_DECISIONS_LOG_PATH, 'a', encoding='utf-8') as f:
            f.write(json.dumps(decision, ensure_ascii=False) + '\n')
    except Exception:
        # 持久化失败不影响主流程
        pass

# 突破与加仓策略的运行时状态
last_breakout_ts = 0.0
last_pyramid_ts = 0.0
pyramid_adds_long = 0
pyramid_adds_short = 0

# 已实现盈亏持久化
REALIZED_PNL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'realized_pnl.json')
os.makedirs(os.path.dirname(REALIZED_PNL_PATH), exist_ok=True)

def load_realized_pnl() -> None:
    global realized_profit_usdt
    try:
        if os.path.exists(REALIZED_PNL_PATH):
            with open(REALIZED_PNL_PATH, 'r', encoding='utf-8') as f:
                data = json.load(f)
                realized_profit_usdt = float(data.get('realized_profit_usdt', 0.0))
    except Exception:
        pass

def save_realized_pnl() -> None:
    try:
        with open(REALIZED_PNL_PATH, 'w', encoding='utf-8') as f:
            json.dump({'realized_profit_usdt': realized_profit_usdt}, f, ensure_ascii=False)
    except Exception:
        pass

# 胜率统计（基于已实现盈亏减去阈值：BTC等值持仓量 * 1.5 USDT）
def update_win_statistics(realized_pnl_usdt: float, size_contracts: float) -> None:
    try:
        ct_size_btc = get_contract_size_btc()
        btc_equiv = float(size_contracts) * float(ct_size_btc)
        threshold_usdt = btc_equiv * 1.5
        perf = web_data.get('performance', {})
        if realized_pnl_usdt - threshold_usdt > 0:
            perf['wins'] = perf.get('wins', 0) + 1
        else:
            perf['losses'] = perf.get('losses', 0) + 1
        total = perf.get('wins', 0) + perf.get('losses', 0)
        perf['total_trades'] = total
        perf['win_rate'] = (perf.get('wins', 0) / total * 100.0) if total > 0 else 0
        web_data['performance'] = perf
    except Exception:
        pass

def _get_okx_price_tick_info():
    """从交易所元数据获取价格tick信息（步长与小数位）。"""
    try:
        market = exchange.market(TRADE_CONFIG['symbol'])
        info = market.get('info', {}) if isinstance(market, dict) else {}
        tick_sz_str = (info.get('tickSz') or info.get('tickSize') or '').strip()
        if tick_sz_str:
            try:
                step = float(tick_sz_str)
            except Exception:
                step = 0.1
            decimals = 0
            if '.' in tick_sz_str:
                decimals = len(tick_sz_str.split('.')[-1].rstrip('0'))
            return step, max(decimals, 0)
        # 回退使用ccxt的precision.price（表示小数位数）
        precision = market.get('precision', {}) if isinstance(market, dict) else {}
        dec = precision.get('price')
        if isinstance(dec, int) and dec >= 0:
            step = 10 ** (-dec) if dec <= 8 else 1e-8
            return step, dec
    except Exception:
        pass
    # 兜底：BTC合约通常tick不少于0.1
    return 0.1, 1

def _format_price_for_okx(px: float) -> str:
    """将价格按tick步长四舍五入并格式化为字符串。"""
    try:
        step, decimals = _get_okx_price_tick_info()
        if step <= 0:
            step = 0.1
            decimals = 1
        # 四舍五入到最近的步长倍数
        rounded = round(round(float(px) / step) * step, max(decimals, 0))
        fmt = f"{{:.{max(decimals, 0)}f}}"
        return fmt.format(rounded)
    except Exception:
        try:
            return f"{float(px):.2f}"
        except Exception:
            return str(px)

def build_okx_tp_sl_params(tp_price: float = None, sl_price: float = None) -> dict:
    """根据OKX v5下单参数，构建附带止盈止损的参数。
    - tpTriggerPx / slTriggerPx: 触发价格
    - tpOrdPx / slOrdPx: 触发后委托价格，-1表示以市价成交
    - tpTriggerPxType / slTriggerPxType: 触发价类型，默认last
    """
    params = {}
    try:
        if tp_price is not None:
            params['tpTriggerPx'] = _format_price_for_okx(tp_price)
            params['tpOrdPx'] = "-1"  # 市价触发
            params['tpTriggerPxType'] = 'last'
        if sl_price is not None:
            params['slTriggerPx'] = _format_price_for_okx(sl_price)
            params['slOrdPx'] = "-1"  # 市价触发
            params['slTriggerPxType'] = 'last'
    except Exception:
        pass
    return params

# 初始余额（用于计算收益率）
initial_balance = None
realized_profit_usdt = 0.0  # 历史已平仓累计盈亏（USDT）
ACCOUNT_POS_MODE = None  # 'net' 或 'long_short'


def setup_exchange():
    """设置交易所参数"""
    try:
        # OKX设置杠杆
        exchange.set_leverage(
            TRADE_CONFIG['leverage'],
            TRADE_CONFIG['symbol'],
            {'mgnMode': 'cross'}  # 全仓模式
        )
        print(f"设置杠杆倍数: {TRADE_CONFIG['leverage']}x")

        # 获取余额
        balance = exchange.fetch_balance()
        usdt_balance = balance['USDT']['free']
        print(f"当前USDT余额: {usdt_balance:.2f}")

        # 读取账户持仓模式（净值/套保），用于是否传 posSide
        try:
            cfg = exchange.privateGetAccountConfig()
            data = (cfg.get('data') or [{}])[0]
            pos_mode_api = (data.get('posMode') or '').lower()  # 期望返回 long_short_mode / net_mode
            global ACCOUNT_POS_MODE
            if 'long' in pos_mode_api:
                ACCOUNT_POS_MODE = 'long_short'
            else:
                ACCOUNT_POS_MODE = 'net'
            print(f"账户持仓模式: {ACCOUNT_POS_MODE}")
        except Exception as _:
            print("账户持仓模式获取失败，默认按净值模式处理")
            ACCOUNT_POS_MODE = 'net'

        return True
    except Exception as e:
        print(f"交易所设置失败: {e}")
        return False


def calculate_technical_indicators(df):
    """计算技术指标 - 来自第一个策略"""
    try:
        # 移动平均线
        df['sma_5'] = df['close'].rolling(window=5, min_periods=1).mean()
        df['sma_20'] = df['close'].rolling(window=20, min_periods=1).mean()
        df['sma_50'] = df['close'].rolling(window=50, min_periods=1).mean()

        # 指数移动平均线
        df['ema_12'] = df['close'].ewm(span=12).mean()
        df['ema_26'] = df['close'].ewm(span=26).mean()
        df['macd'] = df['ema_12'] - df['ema_26']
        df['macd_signal'] = df['macd'].ewm(span=9).mean()
        df['macd_histogram'] = df['macd'] - df['macd_signal']

        # 相对强弱指数 (RSI)
        delta = df['close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs = gain / loss
        df['rsi'] = 100 - (100 / (1 + rs))

        # 布林带
        df['bb_middle'] = df['close'].rolling(20).mean()
        bb_std = df['close'].rolling(20).std()
        df['bb_upper'] = df['bb_middle'] + (bb_std * 2)
        df['bb_lower'] = df['bb_middle'] - (bb_std * 2)
        df['bb_position'] = (df['close'] - df['bb_lower']) / (df['bb_upper'] - df['bb_lower'])

        # 成交量均线
        df['volume_ma'] = df['volume'].rolling(20).mean()
        df['volume_ratio'] = df['volume'] / df['volume_ma']

        # 支撑阻力位
        df['resistance'] = df['high'].rolling(20).max()
        df['support'] = df['low'].rolling(20).min()

        # 填充NaN值
        df = df.bfill().ffill()

        return df
    except Exception as e:
        print(f"技术指标计算失败: {e}")
        return df


def get_support_resistance_levels(df, lookback=20):
    """计算支撑阻力位"""
    try:
        recent_high = df['high'].tail(lookback).max()
        recent_low = df['low'].tail(lookback).min()
        current_price = df['close'].iloc[-1]

        resistance_level = recent_high
        support_level = recent_low

        # 动态支撑阻力（基于布林带）
        bb_upper = df['bb_upper'].iloc[-1]
        bb_lower = df['bb_lower'].iloc[-1]

        return {
            'static_resistance': resistance_level,
            'static_support': support_level,
            'dynamic_resistance': bb_upper,
            'dynamic_support': bb_lower,
            'price_vs_resistance': ((resistance_level - current_price) / current_price) * 100,
            'price_vs_support': ((current_price - support_level) / support_level) * 100
        }
    except Exception as e:
        print(f"支撑阻力计算失败: {e}")
        return {}


def get_sentiment_indicators():
    """获取情绪指标 - 简洁版本"""
    global _sentiment_cache
    try:
        # 使用缓存，降低外部API调用频率
        now_ts = time.time()
        if _sentiment_cache['data'] is not None and (now_ts - _sentiment_cache['ts'] < SENTIMENT_TTL):
            return _sentiment_cache['data']

        # 从环境变量读取（不要硬编码在源码里）
        API_URL = os.getenv('CRYPTO_ORACLE_API_URL', '').strip()
        API_KEY = os.getenv('CRYPTO_ORACLE_API_KEY', '').strip()
        if not API_URL or not API_KEY:
            print("⚠️ 情绪API配置缺失，请在.env设置 CRYPTO_ORACLE_API_URL / CRYPTO_ORACLE_API_KEY")
            return None

        # 获取最近4小时数据
        end_time = datetime.now()
        start_time = end_time - timedelta(hours=4)

        request_body = {
            "apiKey": API_KEY,
            "endpoints": ["CO-A-02-01", "CO-A-02-02"],  # 只保留核心指标
            "startTime": start_time.strftime("%Y-%m-%d %H:%M:%S"),
            "endTime": end_time.strftime("%Y-%m-%d %H:%M:%S"),
            "timeType": TRADE_CONFIG['timeframe'],
            "token": ["BTC"]
        }

        headers = {"Content-Type": "application/json", "X-API-KEY": API_KEY}
        response = requests.post(API_URL, json=request_body, headers=headers)

        if response.status_code == 200:
            data = response.json()
            if data.get("code") == 200 and data.get("data"):
                time_periods = data["data"][0]["timePeriods"]

                # 查找第一个有有效数据的时间段
                for period in time_periods:
                    period_data = period.get("data", [])

                    sentiment = {}
                    valid_data_found = False

                    for item in period_data:
                        endpoint = item.get("endpoint")
                        value = item.get("value", "").strip()

                        if value:  # 只处理非空值
                            try:
                                if endpoint in ["CO-A-02-01", "CO-A-02-02"]:
                                    sentiment[endpoint] = float(value)
                                    valid_data_found = True
                            except (ValueError, TypeError):
                                continue

                    # 如果找到有效数据
                    if valid_data_found and "CO-A-02-01" in sentiment and "CO-A-02-02" in sentiment:
                        positive = sentiment['CO-A-02-01']
                        negative = sentiment['CO-A-02-02']
                        net_sentiment = positive - negative

                        # 正确的时间延迟计算
                        data_delay = int((datetime.now() - datetime.strptime(
                            period['startTime'], '%Y-%m-%d %H:%M:%S')).total_seconds() // 60)

                        print(f"✅ 使用情绪数据时间: {period['startTime']} (延迟: {data_delay}分钟)")

                        data_obj = {
                            'positive_ratio': positive,
                            'negative_ratio': negative,
                            'net_sentiment': net_sentiment,
                            'data_time': period['startTime'],
                            'data_delay_minutes': data_delay
                        }

                        # 写入缓存
                        _sentiment_cache = { 'ts': now_ts, 'data': data_obj }
                        return data_obj

                print("❌ 所有时间段数据都为空")
                return None

        return None
    except Exception as e:
        print(f"情绪指标获取失败: {e}")
        return None


def get_market_trend(df):
    """判断市场趋势"""
    try:
        current_price = df['close'].iloc[-1]

        # 多时间框架趋势分析
        trend_short = "上涨" if current_price > df['sma_20'].iloc[-1] else "下跌"
        trend_medium = "上涨" if current_price > df['sma_50'].iloc[-1] else "下跌"

        # MACD趋势
        macd_trend = "bullish" if df['macd'].iloc[-1] > df['macd_signal'].iloc[-1] else "bearish"

        # 综合趋势判断
        if trend_short == "上涨" and trend_medium == "上涨":
            overall_trend = "强势上涨"
        elif trend_short == "下跌" and trend_medium == "下跌":
            overall_trend = "强势下跌"
        else:
            overall_trend = "震荡整理"

        return {
            'short_term': trend_short,
            'medium_term': trend_medium,
            'macd': macd_trend,
            'overall': overall_trend,
            'rsi_level': df['rsi'].iloc[-1]
        }
    except Exception as e:
        print(f"趋势分析失败: {e}")
        return {}


def get_btc_ohlcv_enhanced():
    """增强版：获取BTC K线数据并计算技术指标"""
    global last_analysis_ts, last_price_data_cache
    try:
        now_ts = time.time()
        # 若分析缓存存在且未过期，复用分析数据，并用最新价格覆盖
        if (last_price_data_cache is not None) and (now_ts - last_analysis_ts < ANALYSIS_UPDATE_INTERVAL):
            try:
                # 仅拉取最新收盘价覆盖
                ohlcv_latest = exchange.fetch_ohlcv(TRADE_CONFIG['symbol'], TRADE_CONFIG['timeframe'], limit=1)
                if ohlcv_latest and len(ohlcv_latest) > 0:
                    last_price_data_cache['price'] = ohlcv_latest[0][4]
                    last_price_data_cache['timestamp'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            except Exception:
                pass
            return last_price_data_cache

        # 否则重新获取整套K线并计算指标（主周期）
        ohlcv = exchange.fetch_ohlcv(TRADE_CONFIG['symbol'], TRADE_CONFIG['timeframe'],
                                     limit=TRADE_CONFIG['data_points'])

        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')

        # 计算技术指标（主周期）
        df = calculate_technical_indicators(df)

        # 计算4H BOLL用于大趋势判定
        trend_4h = None
        boll_4h = None
        try:
            ohlcv_4h = exchange.fetch_ohlcv(TRADE_CONFIG['symbol'], '4h', limit=120)
            df4 = pd.DataFrame(ohlcv_4h, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            df4['timestamp'] = pd.to_datetime(df4['timestamp'], unit='ms')
            # BOLL(20) on 4H
            df4['bb_middle'] = df4['close'].rolling(20).mean()
            bb4_std = df4['close'].rolling(20).std()
            df4['bb_upper'] = df4['bb_middle'] + (bb4_std * 2)
            df4['bb_lower'] = df4['bb_middle'] - (bb4_std * 2)
            df4 = df4.bfill().ffill()
            last4 = df4.iloc[-1]
            price4 = float(last4['close'])
            upper4 = float(last4['bb_upper'])
            lower4 = float(last4['bb_lower'])
            middle4 = float(last4['bb_middle'])
            # 趋势基于价位相对中轨
            if price4 > middle4:
                overall4 = '上涨'
            elif price4 < middle4:
                overall4 = '下跌'
            else:
                overall4 = '震荡'
            # 4H位置百分比
            pos4 = (price4 - lower4) / max((upper4 - lower4), 1e-9)
            trend_4h = {
                'overall': overall4,
                'bb_position': pos4,
                'price': price4
            }
            boll_4h = {
                'bb_upper': upper4,
                'bb_middle': middle4,
                'bb_lower': lower4
            }
        except Exception:
            trend_4h = None
            boll_4h = None

        # 15m数据用于择时与关键位
        levels_15m = None
        kline_15m_data = None
        try:
            ohlcv_15m = exchange.fetch_ohlcv(TRADE_CONFIG['symbol'], '15m', limit=96)
            df15 = pd.DataFrame(ohlcv_15m, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            df15['timestamp'] = pd.to_datetime(df15['timestamp'], unit='ms')
            # 简要指标
            df15['bb_middle'] = df15['close'].rolling(20).mean()
            bb15_std = df15['close'].rolling(20).std()
            df15['bb_upper'] = df15['bb_middle'] + (bb15_std * 2)
            df15['bb_lower'] = df15['bb_middle'] - (bb15_std * 2)
            df15 = df15.bfill().ffill()
            recent_high_15 = df15['high'].tail(20).max()
            recent_low_15 = df15['low'].tail(20).min()
            last15 = df15.iloc[-1]
            levels_15m = {
                'static_resistance': float(recent_high_15),
                'static_support': float(recent_low_15),
                'bb_upper': float(last15['bb_upper']),
                'bb_middle': float(last15['bb_middle']),
                'bb_lower': float(last15['bb_lower'])
            }
            kline_15m_data = df15[['timestamp', 'open', 'high', 'low', 'close', 'volume']].tail(10).to_dict('records')
        except Exception:
            levels_15m = None
            kline_15m_data = None

        current_data = df.iloc[-1]
        previous_data = df.iloc[-2]

        # 获取技术分析数据
        trend_analysis = get_market_trend(df)
        levels_analysis = get_support_resistance_levels(df)

        result = {
            'price': current_data['close'],
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'high': current_data['high'],
            'low': current_data['low'],
            'volume': current_data['volume'],
            'timeframe': TRADE_CONFIG['timeframe'],
            'price_change': ((current_data['close'] - previous_data['close']) / previous_data['close']) * 100,
            'kline_data': df[['timestamp', 'open', 'high', 'low', 'close', 'volume']].tail(10).to_dict('records'),
            'technical_data': {
                'sma_5': current_data.get('sma_5', 0),
                'sma_20': current_data.get('sma_20', 0),
                'sma_50': current_data.get('sma_50', 0),
                'rsi': current_data.get('rsi', 0),
                'macd': current_data.get('macd', 0),
                'macd_signal': current_data.get('macd_signal', 0),
                'macd_histogram': current_data.get('macd_histogram', 0),
                'bb_upper': current_data.get('bb_upper', 0),
                'bb_lower': current_data.get('bb_lower', 0),
                'bb_position': current_data.get('bb_position', 0),
                'volume_ratio': current_data.get('volume_ratio', 0)
            },
            'trend_analysis': trend_analysis,
            'levels_analysis': levels_analysis,
            'trend_4h': trend_4h,
            'boll_4h': boll_4h,
            'levels_15m': levels_15m,
            'kline_15m_data': kline_15m_data,
            'full_data': df
        }

        # 写缓存并记录刷新时间
        last_price_data_cache = result
        last_analysis_ts = now_ts
        return result
    except Exception as e:
        print(f"获取增强K线数据失败: {e}")
        return None


def generate_technical_analysis_text(price_data):
    """生成技术分析文本"""
    if 'technical_data' not in price_data:
        return "技术指标数据不可用"

    tech = price_data['technical_data']
    trend = price_data.get('trend_analysis', {})
    levels = price_data.get('levels_analysis', {})

    # 检查数据有效性
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


def get_current_position():
    """获取当前持仓情况 - OKX版本"""
    try:
        positions = exchange.fetch_positions([TRADE_CONFIG['symbol']])

        for pos in positions:
            if pos['symbol'] == TRADE_CONFIG['symbol']:
                contracts = float(pos['contracts']) if pos['contracts'] else 0

                if contracts > 0:
                    return {
                        'side': pos['side'],  # 'long' or 'short'
                        'size': contracts,
                        'entry_price': float(pos['entryPrice']) if pos['entryPrice'] else 0,
                        'unrealized_pnl': float(pos['unrealizedPnl']) if pos['unrealizedPnl'] else 0,
                        'leverage': float(pos['leverage']) if pos['leverage'] else TRADE_CONFIG['leverage'],
                        'symbol': pos['symbol']
                    }

        return None

    except Exception as e:
        print(f"获取持仓失败: {e}")
        import traceback
        traceback.print_exc()
        return None


def safe_json_parse(json_str):
    """安全解析JSON，处理格式不规范的情况"""
    try:
        return json.loads(json_str)
    except json.JSONDecodeError:
        try:
            # 尝试提取JSON代码块（如果AI包在```json```中）
            if '```json' in json_str:
                start = json_str.find('```json') + 7
                end = json_str.find('```', start)
                if end != -1:
                    json_str = json_str[start:end].strip()
            elif '```' in json_str:
                start = json_str.find('```') + 3
                end = json_str.find('```', start)
                if end != -1:
                    json_str = json_str[start:end].strip()
            
            # 尝试直接解析
            try:
                return json.loads(json_str)
            except:
                pass
            
            # 修复常见的JSON格式问题
            json_str = json_str.replace("'", '"')
            json_str = re.sub(r'(\w+):', r'"\1":', json_str)
            json_str = re.sub(r',\s*}', '}', json_str)
            json_str = re.sub(r',\s*]', ']', json_str)
            return json.loads(json_str)
        except json.JSONDecodeError as e:
            print(f"JSON解析失败，原始内容: {json_str[:200]}")
            print(f"错误详情: {e}")
            return None


def extract_error_info(error):
    """从异常对象提取错误类型与错误码（若可用）。"""
    try:
        error_type = type(error).__name__
        code = None
        try:
            code = getattr(error, 'code', None) or getattr(error, 'status_code', None) or getattr(error, 'http_status', None)
            if code is None:
                resp = getattr(error, 'response', None)
                if resp is not None:
                    code = getattr(resp, 'status_code', None) or getattr(resp, 'status', None)
        except Exception:
            pass
        return error_type, code
    except Exception:
        return 'UnknownError', None


def test_ai_connection():
    """测试AI模型连接状态"""
    global web_data
    try:
        print(f"🔍 测试 {AI_PROVIDER.upper()} 连接...")
        response = ai_client.chat.completions.create(
            model=AI_MODEL,
            messages=[
                {"role": "user", "content": "Hello"}
            ],
            max_tokens=10,
            timeout=10.0
        )
        
        if response and response.choices:
            web_data['ai_model_info']['status'] = 'connected'
            web_data['ai_model_info']['last_check'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            web_data['ai_model_info']['error_message'] = None
            print(f"✓ {AI_PROVIDER.upper()} 连接正常")
            return True
        else:
            web_data['ai_model_info']['status'] = 'error'
            web_data['ai_model_info']['last_check'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            web_data['ai_model_info']['error_message'] = '响应为空'
            print(f"❌ {AI_PROVIDER.upper()} 连接失败: 响应为空")
            return False
            
    except Exception as e:
        err_type, err_code = extract_error_info(e)
        web_data['ai_model_info']['status'] = 'error'
        web_data['ai_model_info']['last_check'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        web_data['ai_model_info']['error_message'] = str(e)
        web_data['ai_model_info']['error_type'] = err_type
        web_data['ai_model_info']['error_code'] = err_code
        code_text = err_code if err_code is not None else 'N/A'
        print(f"❌ {AI_PROVIDER.upper()} 连接失败: [{err_type} {code_text}] {e}")
        return False


def create_fallback_signal(price_data):
    """创建备用交易信号"""
    return {
        "signal": "HOLD",
        "reason": "因技术分析暂时不可用，采取保守策略",
        "stop_loss": price_data['price'] * 0.98,  # -2%
        "take_profit": price_data['price'] * 1.02,  # +2%
        "confidence": "LOW",
        "is_fallback": True
    }


def analyze_with_deepseek(price_data):
    """使用DeepSeek分析市场并生成交易信号（增强版）"""

    # 生成技术分析文本
    technical_analysis = generate_technical_analysis_text(price_data)

    # 构建K线数据文本
    kline_text = f"【最近5根{TRADE_CONFIG['timeframe']}K线数据】\n"
    for i, kline in enumerate(price_data['kline_data'][-5:]):
        trend = "阳线" if kline['close'] > kline['open'] else "阴线"
        change = ((kline['close'] - kline['open']) / kline['open']) * 100
        kline_text += f"K线{i + 1}: {trend} 开盘:{kline['open']:.2f} 收盘:{kline['close']:.2f} 涨跌:{change:+.2f}%\n"

    # 添加上次交易信号
    signal_text = ""
    if signal_history:
        last_signal = signal_history[-1]
        signal_text = f"\n【上次交易信号】\n信号: {last_signal.get('signal', 'N/A')}\n信心: {last_signal.get('confidence', 'N/A')}"

    # 获取情绪数据
    sentiment_data = get_sentiment_indicators()
    # 简化情绪文本（多了没用）
    if sentiment_data:
        sign = '+' if sentiment_data['net_sentiment'] >= 0 else ''
        sentiment_text = f"【市场情绪】乐观{sentiment_data['positive_ratio']:.1%} 悲观{sentiment_data['negative_ratio']:.1%} 净值{sign}{sentiment_data['net_sentiment']:.3f}"
    else:
        sentiment_text = "【市场情绪】数据暂不可用"

    print(sentiment_text)

    # 添加当前持仓信息
    current_pos = get_current_position()
    position_text = "无持仓" if not current_pos else f"{current_pos['side']}仓, 数量: {current_pos['size']}, 盈亏: {current_pos['unrealized_pnl']:.2f}USDT"
    pnl_text = f", 持仓盈亏: {current_pos['unrealized_pnl']:.2f} USDT" if current_pos else ""

    # 多周期信息：4H趋势与15m关键位
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
    except Exception:
        pass

    prompt = f"""
    你是一个专业的加密货币交易分析师。请基于以下BTC/USDT {TRADE_CONFIG['timeframe']}周期数据进行分析：

    {kline_text}

    {technical_analysis}

    {signal_text}

    {sentiment_text}  # 添加情绪分析

    {mtf_text}

    【当前行情】
    - 当前价格: ${price_data['price']:,.2f}
    - 时间: {price_data['timestamp']}
    - 本K线最高: ${price_data['high']:,.2f}
    - 本K线最低: ${price_data['low']:,.2f}
    - 本K线成交量: {price_data['volume']:.2f} BTC
    - 价格变化: {price_data['price_change']:+.2f}%
    - 当前持仓: {position_text}{pnl_text}

    【原则（4H判势 + 15m择时）】
    1. 目标：平均每分钟寻找一次可交易机会，快速进出，追求小利润高频率的复利；多空对称，既可做多也可做空。
    2. 大趋势（4H BOLL）：以4H中轨为趋势锚，价>中轨偏多，价<中轨偏空；尽量顺势，不逆大趋势。
    3. 入场（15m）：在15m关键位（静态支撑/阻力与BOLL）附近等待微结构突破或回归信号择时。
    4. 触发逻辑：请综合判断
    - 最新价上破/下破最近3-5根K线的局部高/低点（微突破）
    - 1-3分钟内的瞬时动量加速（MACD柱翻红/翻绿、RSI快速越界后回归）
    - 贴近布林带外侧的快速回归/延伸
    - 极短周期均线交叉（如 EMA12/EMA26 在1-3根内快速交叉）

    【信号判定 - 必须遵守（先看4H、再看15m）】
    1. 4H方向优先：4H收盘价在BOLL中轨之上更偏BUY，在中轨之下更偏SELL；逆势仅在15m出现强信号时试探，且止损更紧。
    2. 15m择时与关键位：结合15m静态支撑/阻力与BOLL做入场与减仓的触发参考。
    3. 技术/微结构（权重50%）：局部高低点突破、动量加速、短均线结构
    4. 流动性与成交量（权重30%）：量能放大优先，量缩不追
    5. 指标快速信号（权重20%）：RSI极值回归、MACD柱翻转、布林带回归
    6. 信号明确性：
    - 动量向上且上破微结构 → BUY
    - 动量向下且下破微结构 → SELL
    - 无方向或波动极低/点差过大 → HOLD

    【胜率统计口径】
    - 采用已实现盈亏减去阈值后是否为正：profit_adj = realized_pnl_usdt - (btc_equiv * 1.5 USDT)
    - 若 profit_adj > 0 计1次胜，否则计1次败；据此计算 win_rate
    6. 保证胜率：基于已实现盈亏减去阈值：BTC等值持仓量 * 1.5 USDT
    7. 可以做多，也可以做空，只要胜率大于50%即可；原则反向即可；不要局限只能做多，只要有正期望/胜率即可

    【当前技术状况分析】
    - 整体趋势: {price_data['trend_analysis'].get('overall', 'N/A')}
    - 短期趋势: {price_data['trend_analysis'].get('short_term', 'N/A')} 
    - RSI状态: {price_data['technical_data'].get('rsi', 0):.1f} ({'超买' if price_data['technical_data'].get('rsi', 0) > 70 else '超卖' if price_data['technical_data'].get('rsi', 0) < 30 else '中性'})
    - MACD方向: {price_data['trend_analysis'].get('macd', 'N/A')}

    【分析要求】
    基于以上分析，请给出明确的交易信号

    请用以下JSON格式回复：
    {{
        "signal": "BUY|SELL|HOLD",
        "reason": "简要分析理由(包含趋势判断和技术依据)",
        "stop_loss": 具体价格,
        "take_profit": 具体价格, 
        "confidence": "HIGH|MEDIUM|LOW"
    }}
    """

    try:
        print(f"⏳ 正在调用{AI_PROVIDER.upper()} API ({AI_MODEL})...")
        # 允许通过环境变量覆盖模型名称（避免硬编码）
        model_name = os.getenv('DEEPSEEK_MODEL', AI_MODEL)
        response = ai_client.chat.completions.create(
            model=model_name,
            messages=[
                {"role": "system",
                 "content": f"您是一位专业的交易员，专注于{TRADE_CONFIG['timeframe']}周期趋势分析。请结合K线形态和技术指标做出判断，并严格遵循JSON格式要求。"},
                {"role": "user", "content": prompt}
            ],
            stream=False,
            temperature=0.1,
            timeout=60.0  # 将超时提升到60秒，减少timeout概率
        )
        print("✓ API调用成功")
        
        # 更新AI连接状态
        web_data['ai_model_info']['status'] = 'connected'
        web_data['ai_model_info']['last_check'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        web_data['ai_model_info']['error_message'] = None

        # 检查响应
        if not response or not response.choices:
            print(f"❌ {AI_PROVIDER.upper()}返回空响应")
            web_data['ai_model_info']['status'] = 'error'
            web_data['ai_model_info']['error_message'] = '响应为空'
            return create_fallback_signal(price_data)
        
        # 安全解析JSON
        result = response.choices[0].message.content
        if not result:
            print(f"❌ {AI_PROVIDER.upper()}返回空内容")
            return create_fallback_signal(price_data)
            
        print(f"\n{'='*60}")
        print(f"{AI_PROVIDER.upper()}原始回复:")
        print(result)
        print(f"{'='*60}\n")

        # 提取JSON部分
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

        # 验证必需字段
        required_fields = ['signal', 'reason', 'stop_loss', 'take_profit', 'confidence']
        if not all(field in signal_data for field in required_fields):
            missing = [f for f in required_fields if f not in signal_data]
            print(f"⚠️ 缺少必需字段: {missing}，使用备用信号")
            signal_data = create_fallback_signal(price_data)

        # 保存信号到历史记录
        signal_data['timestamp'] = price_data['timestamp']
        signal_history.append(signal_data)
        if len(signal_history) > 30:
            signal_history.pop(0)

        # 信号统计
        signal_count = len([s for s in signal_history if s.get('signal') == signal_data['signal']])
        total_signals = len(signal_history)
        print(f"信号统计: {signal_data['signal']} (最近{total_signals}次中出现{signal_count}次)")

        # 信号连续性检查
        if len(signal_history) >= 3:
            last_three = [s['signal'] for s in signal_history[-3:]]
            if len(set(last_three)) == 1:
                print(f"⚠️ 注意：连续3次{signal_data['signal']}信号")

        return signal_data

    except Exception as e:
        err_type, err_code = extract_error_info(e)
        code_text = err_code if err_code is not None else 'N/A'
        print(f"{AI_PROVIDER.upper()}分析失败: [{err_type} {code_text}] {e}")
        # 更新AI连接状态
        web_data['ai_model_info']['status'] = 'error'
        web_data['ai_model_info']['last_check'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        web_data['ai_model_info']['error_message'] = str(e)
        web_data['ai_model_info']['error_type'] = err_type
        web_data['ai_model_info']['error_code'] = err_code
        return create_fallback_signal(price_data)


def execute_trade(signal_data, price_data):
    """执行交易 - OKX版本（增强防频繁交易保护）"""
    global position, web_data, last_trade_time, last_breakout_ts, last_pyramid_ts, pyramid_adds_long, pyramid_adds_short, realized_profit_usdt

    current_position = get_current_position()

    # 价格突破立即翻转（优先级最高，可绕过反转保护与最小交易间隔，但有独立冷却）
    breakout_triggered = False
    try:
        if int(getattr(config, 'BREAKOUT_ENABLED', 1)) == 1 and price_data.get('levels_analysis'):
            upper = price_data['levels_analysis'].get('static_resistance')
            lower = price_data['levels_analysis'].get('static_support')
            px = float(price_data['price'])
            now_ts = time.time()
            if now_ts - last_breakout_ts >= int(getattr(config, 'BREAKOUT_COOLDOWN_SEC', 60)):
                # 上破
                if upper and upper > 0:
                    thresh = upper * (1.0 + float(getattr(config, 'BREAKOUT_UPPER_PCT', 0.003)) / 100.0)
                    if px >= thresh:
                        print("🚀 突破上轨触发：立即翻转为多头")
                        signal_data = dict(signal_data)
                        signal_data['signal'] = 'BUY'
                        signal_data['confidence'] = 'HIGH'
                        breakout_triggered = True
                # 下破
                if not breakout_triggered and lower and lower > 0:
                    thresh = lower * (1.0 - float(getattr(config, 'BREAKOUT_LOWER_PCT', 0.003)) / 100.0)
                    if px <= thresh:
                        print("📉 跌破下轨触发：立即翻转为空头")
                        signal_data = dict(signal_data)
                        signal_data['signal'] = 'SELL'
                        signal_data['confidence'] = 'HIGH'
                        breakout_triggered = True
                if breakout_triggered:
                    last_breakout_ts = now_ts
    except Exception as _:
        pass

    # ⚖️ 极端偏向翻转：最近18次≥16次同向且无相反方向时尝试反向（不绕过最小交易间隔）
    if not breakout_triggered:
        try:
            recent_signals = signal_history[-3:]
            if recent_signals:
                count_buy = sum(1 for s in recent_signals if s.get('signal') == 'BUY')
                count_sell = sum(1 for s in recent_signals if s.get('signal') == 'SELL')
                dominant = None
                if count_buy >= 2 and count_sell == 0:
                    dominant = 'BUY'
                elif count_sell >= 2 and count_buy == 0:
                    dominant = 'SELL'

                if dominant is not None:
                    target = 'SELL' if dominant == 'BUY' else 'BUY'
                    if signal_data.get('signal') != target:
                        print(f"⚖️ 极端偏向触发：最近3次中{dominant}≥2且无{'SELL' if dominant=='BUY' else 'BUY'} → 翻转为{target}（强制HIGH）")
                        signal_data = dict(signal_data)
                        signal_data['signal'] = target
                        signal_data['confidence'] = 'HIGH'
                        # 附加理由说明
                        try:
                            original_reason = str(signal_data.get('reason', '') or '')
                            dom_count = count_buy if dominant == 'BUY' else count_sell
                            signal_data['reason'] = f"{original_reason} | 极端偏向翻转: 最近3次{dominant}={dom_count}, 反向尝试{target}"
                        except Exception:
                            pass
        except Exception as _:
            pass

    # ⏰ 检查交易间隔（防止过于频繁交易），若刚发生突破触发则放行
    if not breakout_triggered and last_trade_time is not None:
        time_since_last_trade = (datetime.now() - last_trade_time).total_seconds()
        if time_since_last_trade < MIN_TRADE_INTERVAL:
            remaining_time = MIN_TRADE_INTERVAL - time_since_last_trade
            print(f"🔒 距上次交易仅 {time_since_last_trade:.0f} 秒，需等待 {remaining_time:.0f} 秒后才能交易")
            return

    # 🔴 防止频繁反转（突破触发时可绕过）
    if not breakout_triggered and current_position and signal_data['signal'] != 'HOLD':
        current_side = current_position['side']
        # 修正：正确处理HOLD情况
        if signal_data['signal'] == 'BUY':
            new_side = 'long'
        elif signal_data['signal'] == 'SELL':
            new_side = 'short'
        else:  # HOLD
            new_side = None

        # 如果只是方向反转：允许HIGH置信度直接反转（不再要求连续确认）
        if new_side != current_side:
            if signal_data['confidence'] != 'HIGH':
                print(f"🔒 非高信心反转信号，保持现有{current_side}仓")
                return

    print(f"交易信号: {signal_data['signal']}")
    print(f"信心程度: {signal_data['confidence']}")
    print(f"理由: {signal_data['reason']}")
    print(f"止损: ${signal_data['stop_loss']:,.2f}")
    print(f"止盈: ${signal_data['take_profit']:,.2f}")
    print(f"当前持仓: {current_position}")

    # 风险管理：低信心信号不执行（突破触发已强制HIGH）
    if signal_data['confidence'] == 'LOW' and not TRADE_CONFIG['test_mode']:
        print("⚠️ 低信心信号，跳过执行")
        return

    if TRADE_CONFIG['test_mode']:
        print("测试模式 - 仅模拟交易")
        return

    try:
        # 获取可用余额（从缓存读取，减少私有接口调用频率）
        usdt_balance = None
        try:
            cached_balance = web_data.get('account_info', {})
            usdt_balance = cached_balance.get('usdt_balance', None)
        except Exception:
            usdt_balance = None
        if usdt_balance is None:
            balance = exchange.fetch_balance()
            usdt_balance = balance['USDT']['free']

        # 计算下单张数与保证金需求
        base_amount_btc = TRADE_CONFIG['amount']
        desired_contracts = btc_amount_to_okx_contracts(base_amount_btc)
        mark_price = price_data['price']  # 近似用当前价，最好可调用ticker.markPx
        required_margin = estimate_required_margin_usdt(desired_contracts, mark_price, TRADE_CONFIG['leverage'])

        if usdt_balance is None:
            print("⚠️ 可用余额未知，跳过交易以保证安全")
            return

        # 若保证金不足，则按比例下调张数（保留>=1张）
        if required_margin > usdt_balance * 0.8:
            max_contracts = int((usdt_balance * 0.8) / max(estimate_required_margin_usdt(1, mark_price, TRADE_CONFIG['leverage']), 1e-9))
            if max_contracts < 1:
                print(f"⚠️ 保证金不足，跳过交易。需要: {required_margin:.2f} USDT, 可用: {usdt_balance:.2f} USDT")
                return
            print(f"⚠️ 保证金不足，自动将下单张数从 {desired_contracts} 调整为 {max_contracts}")
            desired_contracts = max_contracts

        # 执行交易逻辑   tag 是我的经纪商api（不拿白不拿），不会影响大家返佣，介意可以删除
        # 构建止盈止损参数（基于AI给定的价格）
        try:
            tp_price = signal_data.get('take_profit', None)
            sl_price = signal_data.get('stop_loss', None)
            tp_sl_params = build_okx_tp_sl_params(tp_price, sl_price)
        except Exception:
            tp_sl_params = {}
        if signal_data['signal'] == 'BUY':
            if current_position and current_position['side'] == 'short':
                print("平空仓并开多仓...")
                # 平空仓（按当前持仓合约数）
                params = {'tdMode': 'cross', 'reduceOnly': True, 'tag': '60bb4a8d3416BCDE'}
                if ACCOUNT_POS_MODE == 'long_short':
                    params['posSide'] = 'short'
                # 记录平仓前的入场价与方向，用于计算已实现盈亏
                try:
                    entry = float(current_position.get('entry_price') or 0)
                    size_contracts = float(current_position.get('size') or 0)
                    ct_size_btc = get_contract_size_btc()
                    close_price = mark_price
                    # 空头平仓已实现 = (入场价 - 平仓价) * 张数*合约面值
                    realized = (entry - close_price) * size_contracts * ct_size_btc
                except Exception:
                    realized = 0.0
                exchange.create_order(symbol=TRADE_CONFIG['symbol'], type='market', side='buy', amount=current_position['size'], params=params)
                # 胜率统计（基于本次平仓的已实现盈亏）
                try:
                    update_win_statistics(realized, size_contracts)
                except Exception:
                    pass
                time.sleep(1)
                # 开多仓（将BTC数量换算为合约张数下单）
                long_size = desired_contracts
                params = {'tdMode': 'cross', 'tag': '60bb4a8d3416BCDE'}
                if ACCOUNT_POS_MODE == 'long_short':
                    params['posSide'] = 'long'
                # 合并止盈止损
                try:
                    p2 = dict(params)
                    p2.update(tp_sl_params)
                except Exception:
                    p2 = params
                exchange.create_order(symbol=TRADE_CONFIG['symbol'], type='market', side='buy', amount=long_size, params=p2)
                # 累计已实现盈亏
                realized_profit_usdt += realized
                save_realized_pnl()
            elif current_position and current_position['side'] == 'long':
                # 同向加仓（连涨加仓）
                try:
                    if int(getattr(config, 'PYRAMID_ENABLED', 1)) == 1:
                        now_ts = time.time()
                        if now_ts - last_pyramid_ts >= int(getattr(config, 'PYRAMID_MIN_INTERVAL_SEC', 60)) and pyramid_adds_long < int(getattr(config, 'PYRAMID_MAX_ADDS', 3)):
                            required = int(getattr(config, 'PYRAMID_CONSEC_SIGNALS_FOR_ADD', 2))
                            recent = [s['signal'] for s in signal_history[-(required-1):]] if required > 1 else []
                            if all(sig == 'BUY' for sig in recent):
                                add_ratio = float(getattr(config, 'PYRAMID_ADD_RATIO', 0.5))
                                add_amount_btc = max(0.0, base_amount_btc * add_ratio)
                                if add_amount_btc > 0:
                                    print(f"➕ 连涨加仓BUY，比例{add_ratio:.2f}，下单BTC={add_amount_btc}")
                                    add_contracts = btc_amount_to_okx_contracts(add_amount_btc)
                                    params = {'tdMode': 'cross', 'tag': '60bb4a8d3416BCDE'}
                                    if ACCOUNT_POS_MODE == 'long_short':
                                        params['posSide'] = 'long'
                                    # 合并止盈止损
                                    try:
                                        p3 = dict(params)
                                        p3.update(tp_sl_params)
                                    except Exception:
                                        p3 = params
                                    exchange.create_order(symbol=TRADE_CONFIG['symbol'], type='market', side='buy', amount=add_contracts, params=p3)
                                    pyramid_adds_long += 1
                                    last_pyramid_ts = now_ts
                                    print(f"连涨加仓完成，累计加仓次数(long)={pyramid_adds_long}")
                                else:
                                    print("连涨加仓比例为0，跳过")
                            else:
                                print("未满足连涨加仓的连续BUY次数要求，跳过")
                        else:
                            print("连涨加仓冷却中或达到上限，跳过")
                except Exception as _:
                    pass
                print("已有多头持仓，保持现状")
            else:
                # 无持仓时开多仓
                print("开多仓...")
                long_size = desired_contracts
                params = {'tdMode': 'cross', 'tag': '60bb4a8d3416BCDE'}
                if ACCOUNT_POS_MODE == 'long_short':
                    params['posSide'] = 'long'
                # 合并止盈止损
                try:
                    p1 = dict(params)
                    p1.update(tp_sl_params)
                except Exception:
                    p1 = params
                exchange.create_order(symbol=TRADE_CONFIG['symbol'], type='market', side='buy', amount=long_size, params=p1)

        elif signal_data['signal'] == 'SELL':
            if current_position and current_position['side'] == 'long':
                print("平多仓并开空仓...")
                # 平多仓（按当前持仓合约数）
                params = {'tdMode': 'cross', 'reduceOnly': True, 'tag': '60bb4a8d3416BCDE'}
                if ACCOUNT_POS_MODE == 'long_short':
                    params['posSide'] = 'long'
                # 记录平仓前的入场价与方向，用于计算已实现盈亏
                try:
                    entry = float(current_position.get('entry_price') or 0)
                    size_contracts = float(current_position.get('size') or 0)
                    ct_size_btc = get_contract_size_btc()
                    close_price = mark_price
                    # 多头平仓已实现 = (平仓价 - 入场价) * 张数*合约面值
                    realized = (close_price - entry) * size_contracts * ct_size_btc
                except Exception:
                    realized = 0.0
                exchange.create_order(symbol=TRADE_CONFIG['symbol'], type='market', side='sell', amount=current_position['size'], params=params)
                # 胜率统计（基于本次平仓的已实现盈亏）
                try:
                    update_win_statistics(realized, size_contracts)
                except Exception:
                    pass
                time.sleep(1)
                # 开空仓（将BTC数量换算为合约张数下单）
                short_size = desired_contracts
                params = {'tdMode': 'cross', 'tag': '60bb4a8d3416BCDE'}
                if ACCOUNT_POS_MODE == 'long_short':
                    params['posSide'] = 'short'
                # 合并止盈止损
                try:
                    p4 = dict(params)
                    p4.update(tp_sl_params)
                except Exception:
                    p4 = params
                exchange.create_order(symbol=TRADE_CONFIG['symbol'], type='market', side='sell', amount=short_size, params=p4)
                # 累计已实现盈亏
                realized_profit_usdt += realized
                save_realized_pnl()
            elif current_position and current_position['side'] == 'short':
                # 同向加仓（连跌加仓）
                try:
                    if int(getattr(config, 'PYRAMID_ENABLED', 1)) == 1:
                        now_ts = time.time()
                        if now_ts - last_pyramid_ts >= int(getattr(config, 'PYRAMID_MIN_INTERVAL_SEC', 60)) and pyramid_adds_short < int(getattr(config, 'PYRAMID_MAX_ADDS', 3)):
                            required = int(getattr(config, 'PYRAMID_CONSEC_SIGNALS_FOR_ADD', 2))
                            recent = [s['signal'] for s in signal_history[-(required-1):]] if required > 1 else []
                            if all(sig == 'SELL' for sig in recent):
                                add_ratio = float(getattr(config, 'PYRAMID_ADD_RATIO', 0.5))
                                add_amount_btc = max(0.0, base_amount_btc * add_ratio)
                                if add_amount_btc > 0:
                                    print(f"➕ 连跌加仓SELL，比例{add_ratio:.2f}，下单BTC={add_amount_btc}")
                                    add_contracts = btc_amount_to_okx_contracts(add_amount_btc)
                                    params = {'tdMode': 'cross', 'tag': '60bb4a8d3416BCDE'}
                                    if ACCOUNT_POS_MODE == 'long_short':
                                        params['posSide'] = 'short'
                                    # 合并止盈止损
                                    try:
                                        p5 = dict(params)
                                        p5.update(tp_sl_params)
                                    except Exception:
                                        p5 = params
                                    exchange.create_order(symbol=TRADE_CONFIG['symbol'], type='market', side='sell', amount=add_contracts, params=p5)
                                    pyramid_adds_short += 1
                                    last_pyramid_ts = now_ts
                                    print(f"连跌加仓完成，累计加仓次数(short)={pyramid_adds_short}")
                                else:
                                    print("连跌加仓比例为0，跳过")
                            else:
                                print("未满足连跌加仓的连续SELL次数要求，跳过")
                        else:
                            print("连跌加仓冷却中或达到上限，跳过")
                except Exception as _:
                    pass
                print("已有空头持仓，保持现状")
            else:
                # 无持仓时开空仓
                print("开空仓...")
                short_size = desired_contracts
                params = {'tdMode': 'cross', 'tag': '60bb4a8d3416BCDE'}
                if ACCOUNT_POS_MODE == 'long_short':
                    params['posSide'] = 'short'
                # 合并止盈止损
                try:
                    p6 = dict(params)
                    p6.update(tp_sl_params)
                except Exception:
                    p6 = params
                exchange.create_order(symbol=TRADE_CONFIG['symbol'], type='market', side='sell', amount=short_size, params=p6)

        print("订单执行成功")
        
        # 更新最后交易时间
        last_trade_time = datetime.now()
        
        time.sleep(PRIVATE_UPDATE_INTERVAL)
        # 主动刷新一次持仓（交易撮合后可能存在轻微延迟，轮询几次）
        refreshed_position = None
        for _ in range(3):
            time.sleep(1)
            refreshed_position = get_current_position()
            if refreshed_position:
                break
        print(f"更新后持仓: {refreshed_position}")
        
        # 记录交易历史
        trade_record = {
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'signal': signal_data['signal'],
            'price': price_data['price'],
            'amount': TRADE_CONFIG['amount'],
            'confidence': signal_data['confidence'],
            'reason': signal_data['reason']
        }
        web_data['trade_history'].append(trade_record)
        if len(web_data['trade_history']) > 100:  # 只保留最近100条
            web_data['trade_history'].pop(0)

    except Exception as e:
        print(f"订单执行失败: {e}")
        import traceback
        traceback.print_exc()


def analyze_with_deepseek_with_retry(price_data, max_retries=2):
    """带重试与节流/退避的DeepSeek分析"""
    global last_ai_call_ts, last_ai_decision_cache, ai_backoff_until_ts

    now_ts = time.time()
    # 若在退避期内，直接使用上次成功的决策（或HOLD）
    if now_ts < ai_backoff_until_ts and last_ai_decision_cache is not None:
        print("⏳ DeepSeek处于退避期，复用上次AI决策缓存")
        return last_ai_decision_cache

    # 若距离上次AI调用小于最小间隔，直接复用缓存
    if (now_ts - last_ai_call_ts) < AI_DECISION_INTERVAL and last_ai_decision_cache is not None:
        return last_ai_decision_cache

    # 记录本次调用时间
    last_ai_call_ts = now_ts

    for attempt in range(max_retries):
        try:
            signal_data = analyze_with_deepseek(price_data)
            if signal_data:
                # 成功则更新缓存
                last_ai_decision_cache = signal_data
                ai_backoff_until_ts = 0.0
                return signal_data

            print(f"第{attempt + 1}次尝试失败，进行重试...")
            time.sleep(2)

        except Exception as e:
            print(f"第{attempt + 1}次尝试异常: {e}")
            import traceback
            traceback.print_exc()
            if attempt == max_retries - 1:
                # 设置退避：1分钟
                ai_backoff_until_ts = time.time() + 60
                # 返回缓存或fallback
                return last_ai_decision_cache or create_fallback_signal(price_data)
            time.sleep(2)

    # 最终返回缓存或fallback
    return last_ai_decision_cache or create_fallback_signal(price_data)


def wait_for_next_period():
    """等待到下一个15分钟整点"""
    now = datetime.now()
    current_minute = now.minute
    current_second = now.second

    # 计算下一个整点时间（00, 15, 30, 45分钟）
    next_period_minute = ((current_minute // 15) + 1) * 15
    if next_period_minute == 60:
        next_period_minute = 0

    # 计算需要等待的总秒数
    if next_period_minute > current_minute:
        minutes_to_wait = next_period_minute - current_minute
    else:
        minutes_to_wait = 60 - current_minute + next_period_minute

    seconds_to_wait = minutes_to_wait * 60 - current_second

    # 显示友好的等待时间
    display_minutes = minutes_to_wait - 1 if current_second > 0 else minutes_to_wait
    display_seconds = 60 - current_second if current_second > 0 else 0

    if display_minutes > 0:
        print(f"🕒 等待 {display_minutes} 分 {display_seconds} 秒到整点...")
    else:
        print(f"🕒 等待 {display_seconds} 秒到整点...")

    return seconds_to_wait


def update_realtime_data():
    """实时更新价格和持仓数据（轻量级，不做AI决策）"""
    global web_data, initial_balance, last_private_update_ts
    
    try:
        # 获取当前价格（只获取最新一根K线）
        ohlcv = exchange.fetch_ohlcv(TRADE_CONFIG['symbol'], TRADE_CONFIG['timeframe'], limit=1)
        if ohlcv and len(ohlcv) > 0:
            current_price = ohlcv[0][4]  # 收盘价
            web_data['current_price'] = current_price

        # 节流：仅每 PRIVATE_UPDATE_INTERVAL 秒更新一次余额与持仓（私有接口）
        now_ts = time.time()
        if now_ts - last_private_update_ts >= PRIVATE_UPDATE_INTERVAL:
            # 更新持仓信息
            web_data['current_position'] = get_current_position()

            # 更新账户余额
            balance = exchange.fetch_balance()
            current_equity = balance['USDT']['total']

            # 设置初始余额
            if initial_balance is None:
                initial_balance = current_equity

            web_data['account_info'] = {
                'usdt_balance': balance['USDT']['free'],
                'total_equity': current_equity
            }

            # 更新性能统计
            if web_data['current_position']:
                web_data['performance']['total_profit'] = web_data['current_position'].get('unrealized_pnl', 0)

            # 记录更新时间戳
            last_private_update_ts = now_ts
        
        # 更新时间戳
        web_data['last_update'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
    except Exception as e:
        print(f"⚠️ 实时数据更新失败: {e}")


def trading_bot():
    """主交易机器人函数 - 每分钟执行一次决策"""
    global web_data, initial_balance
    
    print("\n" + "=" * 60)
    print(f"执行时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    # 1. 获取增强版K线数据
    price_data = get_btc_ohlcv_enhanced()
    if not price_data:
        return

    print(f"BTC当前价格: ${price_data['price']:,.2f}")
    print(f"数据周期: {TRADE_CONFIG['timeframe']}")
    print(f"价格变化: {price_data['price_change']:+.2f}%")

    # 2. 使用DeepSeek分析（带重试）
    signal_data = analyze_with_deepseek_with_retry(price_data)

    if signal_data.get('is_fallback', False):
        print("⚠️ 使用备用交易信号")

    # 3. 更新Web数据
    try:
        # 使用缓存的账户信息，避免每秒拉取私有接口
        account_info = web_data.get('account_info', {})
        current_equity = account_info.get('total_equity', None)
        if current_equity is None:
            # 回退：必要时才主动获取
            balance = exchange.fetch_balance()
            current_equity = balance['USDT']['total']
            web_data['account_info'] = {
                'usdt_balance': balance['USDT']['free'],
                'total_equity': current_equity
            }
        
        # 设置初始余额
        if initial_balance is None and current_equity is not None:
            initial_balance = current_equity
        
        # 记录收益曲线数据
        current_position = get_current_position()
        unrealized_pnl = current_position.get('unrealized_pnl', 0) if current_position else 0
        # 总盈亏 = 历史已实现盈亏 + 当前未实现盈亏
        total_profit = realized_profit_usdt + unrealized_pnl
        profit_rate = (total_profit / initial_balance * 100) if initial_balance > 0 else 0
        
        profit_point = {
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'equity': current_equity,
            'profit': total_profit,
            'profit_rate': profit_rate,
            'unrealized_pnl': unrealized_pnl
        }
        web_data['profit_curve'].append(profit_point)
        
        # 只保留最近200个数据点（约50小时）
        if len(web_data['profit_curve']) > 200:
            web_data['profit_curve'].pop(0)
            
    except Exception as e:
        print(f"更新余额失败: {e}")
    
    web_data['current_price'] = price_data['price']
    web_data['current_position'] = get_current_position()
    web_data['last_update'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    # 保存K线数据
    web_data['kline_data'] = price_data['kline_data']
    
    # 保存AI决策
    ai_decision = {
        'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'signal': signal_data['signal'],
        'confidence': signal_data['confidence'],
        'reason': signal_data['reason'],
        'stop_loss': signal_data.get('stop_loss', 0),
        'take_profit': signal_data.get('take_profit', 0),
        'price': price_data['price']
    }
    web_data['ai_decisions'].append(ai_decision)
    # 持久化到文件（无限累积）
    append_ai_decision_to_file(ai_decision)
    
    # 更新性能统计
    try:
        current_position = web_data.get('current_position')
        unrealized_pnl = current_position.get('unrealized_pnl', 0) if current_position else 0
        web_data['performance']['total_profit'] = realized_profit_usdt + unrealized_pnl
    except Exception:
        pass

    # 4. 执行交易
    execute_trade(signal_data, price_data)


def main():
    """主函数"""
    print("BTC/USDT OKX自动交易机器人启动成功！")
    print(f"AI模型: {AI_PROVIDER.upper()} ({AI_MODEL})")
    print("融合技术指标策略 + OKX实盘接口")

    if TRADE_CONFIG['test_mode']:
        print("当前为模拟模式，不会真实下单")
    else:
        print("实盘交易模式，请谨慎操作！")

    print(f"交易周期: {TRADE_CONFIG['timeframe']}")
    print("已启用完整技术指标分析和持仓跟踪功能")

    # 读取历史已实现盈亏
    load_realized_pnl()

    # 设置交易所
    if not setup_exchange():
        print("交易所初始化失败，程序退出")
        return

    print(f"⏰ 执行频率: 每{config.BACKEND_DECISION_INTERVAL_SECONDS}秒进行AI决策分析")
    print(f"📊 数据更新: 每{config.BACKEND_REALTIME_UPDATE_INTERVAL_SECONDS}秒更新一次")
    print("🛡️  安全机制: 有防频繁交易保护，不是每次都交易")

    # 循环执行
    while True:
        try:
            trading_bot()  # 执行AI决策和交易
        except Exception as e:
            print(f"❌ 交易执行出错: {e}")
            import traceback
            traceback.print_exc()
        
        # 执行间隔
        time.sleep(float(config.BACKEND_DECISION_INTERVAL_SECONDS))


if __name__ == "__main__":
    main()
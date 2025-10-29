import os
import time
from datetime import datetime
from .context import AI_PROVIDER, AI_MODEL

# 缓存与运行时状态（从原 deepseekok2.py 抽取）
price_history = []
signal_history = []

last_trade_time = None

# 前后端节流配置（保留原命名，值由 config 提供处使用）
PRIVATE_UPDATE_INTERVAL = None  # 运行时注入
ANALYSIS_UPDATE_INTERVAL = None
AI_DECISION_INTERVAL = None
SENTIMENT_TTL = None

last_private_update_ts = 0.0
last_analysis_ts = 0.0
last_price_data_cache = None

last_ai_call_ts = 0.0
last_ai_decision_cache = None
ai_backoff_until_ts = 0.0

ai_raw_history = []

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
    'profit_curve': [],
    'last_update': None,
    'ai_model_info': {
        'provider': AI_PROVIDER,
        'model': AI_MODEL,
        'status': 'unknown',
        'last_check': None,
        'error_message': None
    },
    'last_portfolio_stats': {
        'signal_stats': {'BUY': 0, 'SELL': 0, 'HOLD': 0},
        'confidence_stats': {'HIGH': 0, 'MEDIUM': 0, 'LOW': 0},
        'total_decisions': 0,
        'timestamp': None
    }
}

initial_balance = None
realized_profit_usdt = 0.0

# 突破与加仓运行状态
last_breakout_ts = 0.0
last_pyramid_ts = 0.0
pyramid_adds_long = 0
pyramid_adds_short = 0

ACCOUNT_POS_MODE = None  # 'net' 或 'long_short'


# ========================
# 多交易对按桶隔离的运行时状态
# ========================

ACTIVE_SYMBOL = None
_SYMBOL_BUCKETS = {}

_BUCKET_KEYS = [
    'last_trade_time',
    'last_private_update_ts',
    'last_analysis_ts',
    'last_price_data_cache',
    'last_ai_call_ts',
    'last_ai_decision_cache',
    'ai_backoff_until_ts',
    'ai_raw_history',
    'signal_history',
    'last_breakout_ts',
    'last_pyramid_ts',
    'pyramid_adds_long',
    'pyramid_adds_short',
    'tpsl_expected',
    'web_snapshot',
    'last_position',
    'last_position_ts',
]

def _default_bucket():
    return {
        'last_trade_time': None,
        'last_private_update_ts': 0.0,
        'last_analysis_ts': 0.0,
        'last_price_data_cache': None,
        'last_ai_call_ts': 0.0,
        'last_ai_decision_cache': None,
        'ai_backoff_until_ts': 0.0,
        'ai_raw_history': [],
        'signal_history': [],
        'last_breakout_ts': 0.0,
        'last_pyramid_ts': 0.0,
        'pyramid_adds_long': 0,
        'pyramid_adds_short': 0,
        'tpsl_expected': {'tp': None, 'sl': None},
        'web_snapshot': {},
        'last_position': None,
        'last_position_ts': 0.0,
    }

def ensure_symbol_bucket(symbol: str):
    s = str(symbol or '').strip()
    if not s:
        return _default_bucket()
    if s not in _SYMBOL_BUCKETS:
        _SYMBOL_BUCKETS[s] = _default_bucket()
    return _SYMBOL_BUCKETS[s]

def _save_globals_to_bucket(symbol: str):
    b = ensure_symbol_bucket(symbol)
    b['last_trade_time'] = last_trade_time
    b['last_private_update_ts'] = last_private_update_ts
    b['last_analysis_ts'] = last_analysis_ts
    b['last_price_data_cache'] = last_price_data_cache
    b['last_ai_call_ts'] = last_ai_call_ts
    b['last_ai_decision_cache'] = last_ai_decision_cache
    b['ai_backoff_until_ts'] = ai_backoff_until_ts
    b['ai_raw_history'] = ai_raw_history
    b['signal_history'] = signal_history
    b['last_breakout_ts'] = last_breakout_ts
    b['last_pyramid_ts'] = last_pyramid_ts
    b['pyramid_adds_long'] = pyramid_adds_long
    b['pyramid_adds_short'] = pyramid_adds_short

def _load_bucket_to_globals(symbol: str):
    b = ensure_symbol_bucket(symbol)
    global last_trade_time, last_private_update_ts, last_analysis_ts, last_price_data_cache
    global last_ai_call_ts, last_ai_decision_cache, ai_backoff_until_ts, ai_raw_history, signal_history
    global last_breakout_ts, last_pyramid_ts, pyramid_adds_long, pyramid_adds_short
    last_trade_time = b.get('last_trade_time')
    last_private_update_ts = b.get('last_private_update_ts', 0.0)
    last_analysis_ts = b.get('last_analysis_ts', 0.0)
    last_price_data_cache = b.get('last_price_data_cache')
    last_ai_call_ts = b.get('last_ai_call_ts', 0.0)
    last_ai_decision_cache = b.get('last_ai_decision_cache')
    ai_backoff_until_ts = b.get('ai_backoff_until_ts', 0.0)
    ai_raw_history = b.get('ai_raw_history') or []
    signal_history = b.get('signal_history') or []
    last_breakout_ts = b.get('last_breakout_ts', 0.0)
    last_pyramid_ts = b.get('last_pyramid_ts', 0.0)
    pyramid_adds_long = b.get('pyramid_adds_long', 0)
    pyramid_adds_short = b.get('pyramid_adds_short', 0)

def set_symbol_tpsl_expected(symbol: str, take_profit, stop_loss):
    try:
        b = ensure_symbol_bucket(symbol)
        b['tpsl_expected'] = {'tp': take_profit, 'sl': stop_loss}
    except Exception:
        pass

def set_symbol_web_snapshot(symbol: str, snapshot: dict):
    try:
        b = ensure_symbol_bucket(symbol)
        b['web_snapshot'] = snapshot or {}
    except Exception:
        pass

def switch_active_symbol(symbol: str):
    global ACTIVE_SYMBOL
    try:
        if ACTIVE_SYMBOL:
            _save_globals_to_bucket(ACTIVE_SYMBOL)
    except Exception:
        pass
    ACTIVE_SYMBOL = symbol
    _load_bucket_to_globals(symbol)


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


